

lib/spug/loop/lilv.so : lilv.cc
	gcc -fPIC -shared -I/usr/local/include/crack-0.12 -l lilv-0 -o \
	   lib/spug/loop/lilv.so \
	   lilv.cc

lilv.cc : ext/lilv.crk
	crack ext/lilv.crk

