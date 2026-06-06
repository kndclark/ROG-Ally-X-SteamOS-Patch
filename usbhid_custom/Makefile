obj-m += usbhid_custom.o
usbhid_custom-y := hid-core.o hiddev.o hid-pidff.o

KDIR ?= /lib/modules/$(shell uname -r)/build

all:
	make -C $(KDIR) M=$(PWD) modules

clean:
	make -C $(KDIR) M=$(PWD) clean
