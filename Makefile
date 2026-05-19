obj-m += hid-asus.o asus-wmi-stub.o

KDIR ?= /lib/modules/$(shell uname -r)/build

all:
	make -C $(KDIR) M=$(PWD) modules

clean:
	make -C $(KDIR) M=$(PWD) clean

sync-upstream:
	@chmod +x ./sync_upstream.sh
	@./sync_upstream.sh

