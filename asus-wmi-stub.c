#include <linux/module.h>
#include "asus-wmi.h"

int asus_hid_register_listener(struct asus_hid_listener *cdev) { return 0; }
EXPORT_SYMBOL_GPL(asus_hid_register_listener);

void asus_hid_unregister_listener(struct asus_hid_listener *cdev) {}
EXPORT_SYMBOL_GPL(asus_hid_unregister_listener);

int asus_hid_event(enum asus_hid_event event) { return 0; }
EXPORT_SYMBOL_GPL(asus_hid_event);

MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("Stub to satisfy hid-asus dependencies on older SteamOS kernels");
