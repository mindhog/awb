
all : lib/spug/loop/lilv.so lib/spug/loop/sysextra.so

lib/spug/loop/lilv.so : lilv.cc
	gcc -fPIC -shared -I/usr/local/include/crack-0.12 -l lilv-0 -o \
	   lib/spug/loop/lilv.so \
	   lilv.cc

lilv.cc : ext/lilv.crk
	crack ext/lilv.crk

lib/spug/loop/sysextra.so : sysextra.cc
	gcc -fPIC -shared -I/usr/local/include/crack-0.12 -l jack -o \
	   lib/spug/loop/sysextra.so \
	   sysextra.cc

sysextra.cc : ext/sysextra.crk
	crack ext/sysextra.crk
