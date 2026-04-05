obj-m += hid-asus-ally.o

KDIR ?= /home/deck/staging/linux

all:
	make -C $(KDIR) M=$(PWD) modules

clean:
	make -C $(KDIR) M=$(PWD) clean
