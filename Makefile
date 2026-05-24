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

configure:
	@if ! git remote get-url nero >/dev/null 2>&1; then \
		echo "Adding remote 'nero'..."; \
		git remote add nero https://github.com/NeroReflex/linux.git; \
	fi

sync-upstream: configure
	@chmod +x ./sync_upstream.sh
	@./sync_upstream.sh

diff-upstream: configure
	@git diff nero/for-next:drivers/hid/hid-asus.c hid-asus.c

