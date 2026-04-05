#include <linux/module.h>
#include <linux/export-internal.h>
#include <linux/compiler.h>

MODULE_INFO(name, KBUILD_MODNAME);

__visible struct module __this_module
__section(".gnu.linkonce.this_module") = {
	.name = KBUILD_MODNAME,
	.init = init_module,
#ifdef CONFIG_MODULE_UNLOAD
	.exit = cleanup_module,
#endif
	.arch = MODULE_ARCH_INIT,
};


MODULE_INFO(depends, "led-class-multicolor,ff-memless,hid-asus");

MODULE_ALIAS("hid:b0003g*v00000B05p00001ABE");
MODULE_ALIAS("hid:b0003g*v00000B05p00001B4C");

MODULE_INFO(srcversion, "C42C4C03584A03B0CE1C05A");
