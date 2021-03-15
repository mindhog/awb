"""AWB client implementation for python programs.

This contains both the client layer for communicating to AWB applications and
the general code for doing simple protobuf RPCs in python.  The general
protobuf RPC code should eventually be refactored out.
"""

from google.protobuf.message import Message
from midi import Track
from midifile import Reader as MidiReader, Writer as MidiWriter
from spug.io.reactor import Address, INETAddress
from spug.io.proactor import getProactor, DataHandler, INETAddress
from threading import Condition
from typing import Callable, Dict, Tuple, Type
from awb_pb2 import GetMidiRequest, GetMidiResponse, RPCMessage, \
    PutMidiRequest

class NotEnoughData(Exception):
    """Raised by deserialization functions when the buffer does not contain
    enough data to deserialize a complete object.
    """
    pass

class RemoteError(Exception):
    """Raised with an error message sent from the remote system."""
    pass

def read_proto_varint(data: bytes, pos: int) -> Tuple[int, int]:
    """Returns the integer and the index of the byte after the encoded integer
    in 'data'.

    Throws NotEnoughData() if bytes isn't long enough to contain a complete
    varint.
    """
    b = 0x80
    val = 0
    offset = 0
    while b & 0x80:
        # make sure we've got data
        if pos >= len(data):
            raise NotEnoughData()

        # see if we've got the last byte
        b = data[pos]
        pos += 1
        val = val | ((b & 0x7f) << offset);
        offset += 7;

    lastUInt = val
    return lastUInt, pos

def encode_proto_varint(val: int) -> bytes:
    if not val:
        return b'\0'

    buf = []
    while val:
        b = val & 0x7f
        val >>= 7
        if val:
            b |= 0x80;
        buf.append(b);

    return bytes(buf)

def read_proto_string(data: bytes, pos: int) -> Tuple[bytes, int]:
    size, i = read_proto_varint(data, pos)
    pos = i + size
    if len(data) <= pos:
        return data[i:pos], pos
    else:
        raise NotEnoughData()

class RPCContext:

    def __init__(self, handler: 'RPCHandler') -> None:
        self.handler = handler

    def send_response(self, id: int, response: bytes) -> None:
        self.handler.serialize(RPCMessage(id=id, response=response))

    def send_error(self, id: int, error: bytes) -> None:
        self.handler.serialize(RPCMessage(id=id, error=error))

class MethodWrapper:

    def __init__(self, func: Callable[[RPCContext, RPCMessage], RPCMessage],
                 request_type: Type,
                 response_type: Type) -> None:
        self.__func = func
        self.__request_type = request_type
        self.__response_Type = response_type

    def __call__(self, context: RPCContext, request: RPCMessage) -> RPCMessage:
        req = self.__request_type()
        req.MergeFromString(request.request)
        try:
            resp = self.__func(context, req)
            context.send_response(request.id, resp.SerializeToString())
        except Exception as ex:
            context.send_error(request.id, str(ex))

# Method objects should accept a context and the original RPCMessage (whose
# "request" field they are responsible for deserializing to the appropriate
# native type).  They are also responsible for sending a response back
# through the RPCContext object.
MethodDict = Dict[bytes, Callable[[RPCContext, RPCMessage], None]]

class RPCHandler(DataHandler):
    """Communications thread."""

    __outBuf : bytes
    __inBuf : bytes

    def __init__(self, methods: MethodDict):
        self.__outBuf = b''
        self.__inBuf = b''
        self.__close_flag = False
        self.__methods = methods
        self.__waiters : Dict[int, Callable[[RPCMessage], None]] = {}
        self.__last_id = 0

    def readyToGet(self) -> bool:
        return bool(self.__outBuf)

    def readyToPut(self) -> bool:
        return True

    def readyToClose(self) -> bool:
        return self.__close_flag

    def peek(self, size: int) -> bytes:
        return self.__outBuf[:size]

    def get(self, size: int) -> None:
        self.__outBuf = self.__outBuf[size:]

    def put(self, data: bytes) -> None:
        self.__inBuf += data
        self.process()

    def process(self):
        while self.__inBuf:
            try:
                message, index = read_proto_string(self.__inBuf, 0)
            except NotEnoughData as ex:
                return

            self.__inBuf = self.__inBuf[index:]

            rpc_message = RPCMessage()
            rpc_message.MergeFromString(message)
            if rpc_message.method:
                # This is a request.
                method = self.__methods.get(rpc_message.method)
                if method is None:
                    print(f'Unknown method {rpc_message.method} received')
                    self.serialize(RPCMessage(
                        id=rpc_message.id,
                        error=f'Unknown method {rpc_message.method} received'
                    ))
                    continue

                method(self.__context, rpc_message)
            else:
                # This is a response.
                waiter = self.__waiters.get(rpc_message.id)
                if waiter is None:
                    print(f'Got response for message id {rpc_message.id}, '
                          'which is not waiting for a response.')
                    continue

                waiter(rpc_message)

    def write(self, data: bytes) -> None:
        self.__outBuf += data

    def serialize(self, message: RPCMessage) -> None:
        data = message.SerializeToString()
        self.write(encode_proto_varint(len(data)) + data)

    def __get_next_id(self):
        self.__last_id += 1
        return self.__last_id

    def send(self, method: str, request: Message,
             waiter: Callable[[RPCMessage], None]) -> None:
        """Send a method invocation message to the peer."""
        message = RPCMessage(id=self.__get_next_id(), method=method,
                             request=request.SerializeToString()
                             )
        self.__waiters[message.id] = waiter
        self.serialize(message)

class Waiter:
    """A callable that allows another thread to wait for an RPC response."""

    def __init__(self):
        self.__error = None
        self.__response = None
        self.__cond = Condition()

    def getResponse(self) -> Message:
        """Wait on a reply message and return the response object.

        Throws a RemoteError if an error response was sent.
        """
        with self.__cond:
            self.__cond.wait()
            if self.__error:
                raise RemoteError(self.__error)
            return self.__response

    def __call__(self, message: RPCMessage) -> None:
        with self.__cond:
            self.__error = message.error
            self.__response = message.response
            self.__cond.notify()

class AWBProxy:
    """A proxy object for a remote instance implementing the AWB API."""

    def __init__(self, address: Address) -> None:
        self.__handler = RPCHandler({})
        proactor = getProactor()
        self.__conn = proactor.makeConnection(address, self.__handler)
        self.__control = proactor.makeControlQueue(
            lambda info: self.__handler.send(*info)
        )

    def __send(self, method: str, request: Message,
               waiter: Callable[[RPCMessage], None]
               ) -> None:
        self.__control.add((method, request, waiter))

    def getMidi(self, name: bytes) -> Track:
        waiter = Waiter()
        self.__send('getMidiTrack', GetMidiRequest(name=name), waiter)
        response = GetMidiResponse()
        response.MergeFromString(waiter.getResponse())
        return MidiReader(None).parseTrack(response.contents, response.name)

    def putMidi(self, name: bytes, track: Track) -> None:
        track_data = MidiWriter(None).encodeEvents(track)
        waiter = Waiter()
        self.__send('putMidiTrack',
                    PutMidiRequest(name=name, contents=track_data),
                    waiter
                    )
        return waiter.getResponse()

if __name__ == '__main__':
    from midi import NoteOn, NoteOff
    from midifile import EndTrack
    from threading import Thread

    class MyEndTrack(EndTrack):

        def asMidiString(self, status: int):
            return 0, b'\xff\x2f\0'

    # This has to happen before we start running the proactor otherwise the
    # proactor will quit since it has no objects.
    proxy = AWBProxy(INETAddress('127.0.0.1', 9021))

    proactor_thread = Thread(target=lambda: getProactor().run())
    proactor_thread.start()

    track = Track('Foo', [
        NoteOn(0, 0, 40, 100),
        NoteOn(0, 0, 44, 100),
        NoteOn(0, 0, 47, 100),
        NoteOff(2880, 0, 40, 100),
        NoteOff(2880, 0, 44, 100),
        NoteOff(2880, 0, 47, 100),
        MyEndTrack(5760)
    ])
    print(track[0])

    proxy.getMidi(b'Metronome')
    proxy.putMidi(b'Foo', track)
    print('all done')

