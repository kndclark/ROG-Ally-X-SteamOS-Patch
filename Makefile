obj-m += hid-asus.o
ccflags-y += -include $(PWD)/include/linux/platform_data/x86/asus-wmi.h

KDIR ?= /lib/modules/$(shell uname -r)/build

all:
	make -C $(KDIR) M=$(PWD) modules

clean:
	make -C $(KDIR) M=$(PWD) clean
