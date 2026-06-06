obj-m += hid-asus.o asus-wmi-stub.o

KDIR ?= /lib/modules/$(shell uname -r)/build

all:
	make -C $(KDIR) M=$(PWD) modules

clean:
	make -C $(KDIR) M=$(PWD) clean

test:
	@echo " Running Ally X LED Tests...\
 @echo \Note: Tests require root privileges and access to /dev/hidraw2\
 @echo \Please run individual tests manually e.g.:\
 @echo " sudo python3 tests/test_flicker.py /dev/hidraw2\
