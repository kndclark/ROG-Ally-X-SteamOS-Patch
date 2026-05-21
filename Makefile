obj-m += hid-asus.o asus-wmi-stub.o

KDIR ?= /lib/modules/$(shell uname -r)/build

all:
	make -C $(KDIR) M=$(PWD) modules

clean:
	@if [ -d "$(KDIR)" ]; then \
		make -C $(KDIR) M=$(PWD) clean; \
	else \
		echo "Warning: KDIR ($(KDIR)) not found, cleaning local files manually."; \
		rm -f *.o *.ko *.mod* *.mod *.a .*.cmd *.symvers *.order *.ko.zst; \
		rm -rf .tmp_versions; \
	fi

sync-upstream:
	@chmod +x ./sync_upstream.sh
	@./sync_upstream.sh

diff-upstream:
	@git diff nero/for-next:drivers/hid/hid-asus.c hid-asus.c

