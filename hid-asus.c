// SPDX-License-Identifier: GPL-2.0-or-later
/*
 *  HID driver for Asus notebook built-in keyboard.
 *  Fixes small logical maximum to match usage maximum.
 *
 *  Currently supported devices are:
 *    EeeBook X205TA
 *    VivoBook E200HA
 *
 *  Copyright (c) 2016 Yusuke Fujimaki <usk.fujimaki@gmail.com>
 *
 *  This module based on hid-ortek by
 *  Copyright (c) 2010 Johnathon Harris <jmharris@gmail.com>
 *  Copyright (c) 2011 Jiri Kosina
 *
 *  This module has been updated to add support for Asus i2c touchpad.
 *
 *  Copyright (c) 2016 Brendan McGrath <redmcg@redmandi.dyndns.org>
 *  Copyright (c) 2016 Victor Vlasenko <victor.vlasenko@sysgears.com>
 *  Copyright (c) 2016 Frederik Wenigwieser <frederik.wenigwieser@gmail.com>
 */

#include <linux/acpi.h>
#include <linux/cleanup.h>
#include <linux/device.h>
#include <linux/dmi.h>
#include <linux/hid.h>
#include <linux/jiffies.h>
#include <linux/list.h>
#include <linux/module.h>
#include <linux/mutex.h>
#include "asus-wmi.h" // TODO relative pathing removed for Ally X hardware module patch testing
#include <linux/types.h>
#include <linux/input/mt.h>
#include <linux/usb.h> /* For to_usb_interface for T100 touchpad intf check */
#include <linux/power_supply.h>
#include <linux/stddef.h>
#include <linux/sysfs.h>
#include <linux/leds.h>
#include <linux/led-class-multicolor.h>

#include "hid-ids.h"

MODULE_AUTHOR("Yusuke Fujimaki <usk.fujimaki@gmail.com>");
MODULE_AUTHOR("Brendan McGrath <redmcg@redmandi.dyndns.org>");
MODULE_AUTHOR("Victor Vlasenko <victor.vlasenko@sysgears.com>");
MODULE_AUTHOR("Frederik Wenigwieser <frederik.wenigwieser@gmail.com>");
MODULE_DESCRIPTION("Asus HID Keyboard and TouchPad");

#define T100_TPAD_INTF 2
#define MEDION_E1239T_TPAD_INTF 1

#define E1239T_TP_TOGGLE_REPORT_ID 0x05
#define T100CHI_MOUSE_REPORT_ID 0x06
#define FEATURE_REPORT_ID 0x0d
#define INPUT_REPORT_ID 0x5d
#define FEATURE_KBD_REPORT_ID 0x5a
#define FEATURE_KBD_REPORT_SIZE 64
#define FEATURE_KBD_LED_REPORT_ID1 0x5d
#define FEATURE_KBD_LED_REPORT_ID2 0x5e

#define ROG_ALLY_REPORT_SIZE 64
#define ROG_ALLY_X_MIN_MCU 313
#define ROG_ALLY_MIN_MCU 319

#define HID_ALLY_INTF_KEYBOARD_IN 0x81
#define HID_ALLY_INTF_CFG_IN 0x83
#define HID_ALLY_X_INTF_IN 0x87

#define HID_ALLY_GET_REPORT_ID 0x0D
#define HID_ALLY_SET_REPORT_ID 0x5A
#define HID_ALLY_FEATURE_CODE_PAGE 0xD1

#define HID_ALLY_X_INPUT_REPORT_SIZE 16
#define HID_ALLY_X_INPUT_REPORT 0x0B

#define HID_ALLY_READY_MAX_TRIES 6

/* Spurious HID codes sent by QUIRK_ROG_NKEY_KEYBOARD devices */
#define ASUS_SPURIOUS_CODE_0XEA 0xea
#define ASUS_SPURIOUS_CODE_0XEC 0xec
#define ASUS_SPURIOUS_CODE_0X02 0x02
#define ASUS_SPURIOUS_CODE_0X8A 0x8a
#define ASUS_SPURIOUS_CODE_0X9E 0x9e

/* Special key codes */
#define ASUS_FAN_CTRL_KEY_CODE 0xae

#define SUPPORT_KBD_BACKLIGHT BIT(0)

#define MAX_TOUCH_MAJOR 8
#define MAX_PRESSURE 128

#define BTN_LEFT_MASK 0x01
#define CONTACT_TOOL_TYPE_MASK 0x80
#define CONTACT_X_MSB_MASK 0xf0
#define CONTACT_Y_MSB_MASK 0x0f
#define CONTACT_TOUCH_MAJOR_MASK 0x07
#define CONTACT_PRESSURE_MASK 0x7f

#define	BATTERY_REPORT_ID	(0x03)
#define	BATTERY_REPORT_SIZE	(1 + 8)
#define	BATTERY_LEVEL_MAX	((u8)255)
#define	BATTERY_STAT_DISCONNECT	(0)
#define	BATTERY_STAT_CHARGING	(1)
#define	BATTERY_STAT_FULL	(2)

#define QUIRK_FIX_NOTEBOOK_REPORT	BIT(0)
#define QUIRK_NO_INIT_REPORTS		BIT(1)
#define QUIRK_SKIP_INPUT_MAPPING	BIT(2)
#define QUIRK_IS_MULTITOUCH		BIT(3)
#define QUIRK_NO_CONSUMER_USAGES	BIT(4)
#define QUIRK_USE_KBD_BACKLIGHT		BIT(5)
#define QUIRK_T100_KEYBOARD		BIT(6)
#define QUIRK_T100CHI			BIT(7)
#define QUIRK_G752_KEYBOARD		BIT(8)
#define QUIRK_T90CHI			BIT(9)
#define QUIRK_MEDION_E1239T		BIT(10)
#define QUIRK_ROG_NKEY_KEYBOARD		BIT(11)
#define QUIRK_ROG_CLAYMORE_II_KEYBOARD	BIT(12)
#define QUIRK_ROG_ALLY_XPAD		BIT(13)
#define QUIRK_HID_FN_LOCK		BIT(14)

/*
 * TODO: LED name for SteamOS GameMode visibility.
 * Currently uses "go_s" to spoof a Lenovo Legion Go S and trigger the
 * joystick LED menu in Steam Client. Replace with "asus:rgb:joystick_rings"
 * once the Steam Client UI adds a native ASUS profile.
 */
#define ALLY_LED_NAME "go_s:rgb:joystick_rings"

/* Ally LED report commands (Report ID 0x5a) */
#define ALLY_LED_CMD_CONFIG	0xb3
#define ALLY_LED_CMD_APPLY	0xb4
#define ALLY_LED_CMD_SET	0xb5

/* Ally LED brightness report (Report ID 0x5d) */
#define ALLY_LED_BRIGHTNESS_CMD1	0xba
#define ALLY_LED_BRIGHTNESS_CMD2	0xc5
#define ALLY_LED_BRIGHTNESS_CMD3	0xc4

/* Ally LED effect speed (hardware register values) */
#define ALLY_LED_SPEED_SLOW	0xe1	/* ~13 seconds per cycle */
#define ALLY_LED_SPEED_MED	0xe4	/* ~9 seconds per cycle */
#define ALLY_LED_SPEED_FAST	0xef	/* ~5 seconds per cycle */

enum ally_rgb_effect {
	ALLY_RGB_EFFECT_STATIC		= 0,
	ALLY_RGB_EFFECT_BREATHING	= 1,
	ALLY_RGB_EFFECT_COLOR_CYCLE	= 2,
	ALLY_RGB_EFFECT_RAINBOW		= 3,
	ALLY_RGB_EFFECT_STROBE		= 4,
	ALLY_RGB_EFFECT_COUNT,
};

/* Ally LED effect packet (Report ID 0x5a, command 0xb3) */
struct ally_rgb_report {
	u8 report_id;		/* FEATURE_KBD_REPORT_ID (0x5a) */
	u8 cmd;			/* ALLY_LED_CMD_CONFIG (0xb3) */
	u8 zone;		/* LED zone (0x00 = all) */
	u8 effect;		/* enum ally_rgb_effect value */
	u8 red;			/* red color component */
	u8 green;		/* green color component */
	u8 blue;		/* blue color component */
	u8 speed;		/* animation speed (ALLY_LED_SPEED_*) */
	u8 direction;		/* 0x01 = forward */
	u8 pad1;
	u8 bg_red;		/* background red (0x00 = off) */
	u8 bg_green;		/* background green */
	u8 bg_blue;		/* background blue */
} __packed;

/*
 * Map compact enum index → hardware wire byte (B3 offset 3).
 * Decouples the sysfs string index from the non-sequential HW values.
 * For effects 0–3 the wire byte == enum index (no change to existing behavior);
 * Strobe diverges: enum index 4 → wire byte 0x0A.
 */
static const u8 ally_rgb_effect_wire[] = {
	[ALLY_RGB_EFFECT_STATIC]      = 0x00,
	[ALLY_RGB_EFFECT_BREATHING]   = 0x01,
	[ALLY_RGB_EFFECT_COLOR_CYCLE] = 0x02,
	[ALLY_RGB_EFFECT_RAINBOW]     = 0x03,
	[ALLY_RGB_EFFECT_STROBE]      = 0x0a,
};

#define I2C_KEYBOARD_QUIRKS			(QUIRK_FIX_NOTEBOOK_REPORT | \
						 QUIRK_NO_INIT_REPORTS | \
						 QUIRK_NO_CONSUMER_USAGES)
#define I2C_TOUCHPAD_QUIRKS			(QUIRK_NO_INIT_REPORTS | \
						 QUIRK_SKIP_INPUT_MAPPING | \
						 QUIRK_IS_MULTITOUCH)

#define TRKID_SGN       ((TRKID_MAX + 1) >> 1)

#define ALLY_DEVICE_ATTR_RO(_name, _sysfs_name)    \
	struct device_attribute dev_attr_##_name = \
		__ATTR(_sysfs_name, 0444, _name##_show, NULL)

#define ALLY_DEVICE_ATTR_WO(_name, _sysfs_name)			\
	struct device_attribute dev_attr_##_name =			\
		__ATTR(_sysfs_name, 0200, NULL, _name##_store)

#define ALLY_DEVICE_ATTR_RW(_name, _sysfs_name)			\
	struct device_attribute dev_attr_##_name =			\
		__ATTR(_sysfs_name, 0644, _name##_show, _name##_store)

#define ALLY_DEVICE_CONST_ATTR_RO(fname, sysfs_name, value)		\
	static ssize_t fname##_show(struct device *dev,				\
				   struct device_attribute *attr, char *buf)	\
	{									\
		return sprintf(buf, value);					\
	}									\
	struct device_attribute dev_attr_##fname =				\
		__ATTR(sysfs_name, 0444, fname##_show, NULL)



struct asus_kbd_leds {
	struct asus_hid_listener listener;
	struct hid_device *hdev;
	struct work_struct work;
	unsigned int brightness;
	spinlock_t lock;
	bool removed;
};

struct asus_touchpad_info {
	int max_x;
	int max_y;
	int res_x;
	int res_y;
	int contact_size;
	int max_contacts;
	int report_size;
};

/* ROG Ally LED ring device (one per physical controller) */
struct ally_rgb_dev {
	struct hid_device *hdev;
	struct led_classdev_mc led_rgb_dev;
	struct delayed_work work;
	struct delayed_work resume_work;
	bool output_worker_initialized;
	/* protects: removed, update_rgb, update_effect */
	spinlock_t lock;
	bool removed;
	bool update_rgb;
	bool update_effect;
	/* Last 0x5d hardware level; -1 forces resend. */
	int last_hw_level;
};

/*
 * Persistent LED state — survives suspend/resume and device re-creation.
 *
 * The ROG Ally X fully re-probes (destroys and recreates) the USB device on
 * resume. All devm-managed memory including the led_classdev, subled_info,
 * and mc_subled intensity/brightness fields are freed during this process.
 * This module-level cache is the only mechanism to restore LED state across
 * a suspend/resume cycle on this hardware.
 */
struct ally_rgb_data {
	enum ally_rgb_effect mode;
	u8 speed;		/* 0-100, mapped to 3 discrete HW levels in apply_effect */
	/* 
	 * Minimal intensity cache needed to survive Ally X USB re-probe. 
	 * The Multicolor LED framework does not restore subled intensities 
	 * to new device instances automatically.
	 */
	u8 red;
	u8 green;
	u8 blue;
	bool enabled;
	bool initialized;
};
struct ally_joystick_resp_curve_param {
	u8 move;
	u8 resp;
} __packed;

struct ally_joystick_resp_curve {
	struct ally_joystick_resp_curve_param entry_1;
	struct ally_joystick_resp_curve_param entry_2;
	struct ally_joystick_resp_curve_param entry_3;
	struct ally_joystick_resp_curve_param entry_4;
} __packed;

/* Button identifiers for the turbo attribute system */
enum ally_button_id {
	ALLY_BTN_A,
	ALLY_BTN_B,
	ALLY_BTN_X,
	ALLY_BTN_Y,
	ALLY_BTN_LB,
	ALLY_BTN_RB,
	ALLY_BTN_DU,
	ALLY_BTN_DD,
	ALLY_BTN_DL,
	ALLY_BTN_DR,
	ALLY_BTN_J0B,
	ALLY_BTN_J1B,
	ALLY_BTN_MENU,
	ALLY_BTN_VIEW,
	ALLY_BTN_M1,
	ALLY_BTN_M2,
	ALLY_BTN_MAX
};

/* Names for the button directories in sysfs */
static const char *const ally_button_names[ALLY_BTN_MAX] = {
	[ALLY_BTN_A] = "btn_a",
	[ALLY_BTN_B] = "btn_b",
	[ALLY_BTN_X] = "btn_x",
	[ALLY_BTN_Y] = "btn_y",
	[ALLY_BTN_LB] = "btn_lb",
	[ALLY_BTN_RB] = "btn_rb",
	[ALLY_BTN_DU] = "dpad_up",
	[ALLY_BTN_DD] = "dpad_down",
	[ALLY_BTN_DL] = "dpad_left",
	[ALLY_BTN_DR] = "dpad_right",
	[ALLY_BTN_J0B] = "btn_l3",
	[ALLY_BTN_J1B] = "btn_r3",
	[ALLY_BTN_MENU] = "btn_menu",
	[ALLY_BTN_VIEW] = "btn_view",
	[ALLY_BTN_M1] = "btn_m1",
	[ALLY_BTN_M2] = "btn_m2",
};

/*
 * Button turbo parameters structure
 * Each button can have:
 * - turbo: Turbo press interval in multiples of 50ms (0 = disabled, 1-20 = 50ms-1000ms)
 * - toggle: Toggle interval (0 = disabled)
 */
struct ally_btn_turbo_params {
	u8 turbo;
	u8 toggle;
} __packed;

/* Collection of all button turbo settings */
struct ally_turbo_config {
	struct ally_btn_turbo_params btn_du;
	struct ally_btn_turbo_params btn_dd;
	struct ally_btn_turbo_params btn_dl;
	struct ally_btn_turbo_params btn_dr;
	struct ally_btn_turbo_params btn_j0b;
	struct ally_btn_turbo_params btn_j1b;
	struct ally_btn_turbo_params btn_lb;
	struct ally_btn_turbo_params btn_rb;
	struct ally_btn_turbo_params btn_a;
	struct ally_btn_turbo_params btn_b;
	struct ally_btn_turbo_params btn_x;
	struct ally_btn_turbo_params btn_y;
	struct ally_btn_turbo_params btn_view;
	struct ally_btn_turbo_params btn_menu;
	struct ally_btn_turbo_params btn_m2;
	struct ally_btn_turbo_params btn_m1;
};

struct ally_btn_sysfs_entry;

struct ally_config {
	/* Must be locked if the data is being changed */
	struct mutex config_mutex;
	bool initialized;

	/* Device capabilities flags */
	bool is_ally_x;
	bool xbox_controller_support;
	bool user_cal_support;
	bool turbo_support;
	bool resp_curve_support;
	bool dir_to_btn_support;
	bool gyro_support;
	bool anti_deadzone_support;

	/* Current settings */
	bool xbox_controller_enabled;
	u8 gamepad_mode;
	u8 left_deadzone;
	u8 left_outer_threshold;
	u8 right_deadzone;
	u8 right_outer_threshold;
	u8 left_anti_deadzone;
	u8 right_anti_deadzone;
	u8 left_trigger_min;
	u8 left_trigger_max;
	u8 right_trigger_min;
	u8 right_trigger_max;

	/* Vibration settings */
	u8 vibration_intensity_left;
	u8 vibration_intensity_right;
	bool vibration_active;

	struct ally_turbo_config turbo;
	struct ally_btn_sysfs_entry *button_entries;
	void *button_mappings; /* ally_button_mapping array indexed by gamepad_mode */

	struct ally_joystick_resp_curve left_curve;
	struct ally_joystick_resp_curve right_curve;
};

/* XInput force-feedback report (output report 0x0d, gamepad interface) */
struct ff_data {
	u8 enable;
	u8 magnitude_left;
	u8 magnitude_right;
	u8 magnitude_strong;
	u8 magnitude_weak;
	u8 pulse_sustain_10ms;
	u8 pulse_release_10ms;
	u8 loop_count;
} __packed;

struct ff_report {
	u8 report_id;
	struct ff_data ff;
} __packed;

struct ally_handheld {
	/* All read/write to IN interfaces must lock */
	struct mutex intf_mutex;
	struct hid_device *cfg_hdev;

	struct input_dev *ally_x_input;
	struct hid_device *ally_x_hdev;

	struct ff_report ff_packet;
	struct work_struct ff_work;
	/* Serializes ff_packet and update_ff between play_effect and ff_work */
	spinlock_t ff_lock;
	bool ff_work_initialized;
	bool update_ff;

	struct hid_device *keyboard_hdev;
	struct input_dev *keyboard_input;

	u8 cad_sequence_state;
	unsigned long cad_last_event_time;

	struct ally_config *config;

	/* Ally joystick ring RGB control */
	struct ally_rgb_dev *led_rgb_dev;
	struct ally_rgb_data led_rgb_data;
};

struct asus_drvdata {
	unsigned long quirks;
	struct hid_device *hdev;
	struct input_dev *input;
	struct input_dev *tp_kbd_input;
	struct asus_kbd_leds *kbd_backlight;
	struct ally_handheld *rog_ally;
	const struct asus_touchpad_info *tp;
	struct power_supply *battery;
	struct power_supply_desc battery_desc;
	int battery_capacity;
	int battery_stat;
	bool battery_in_query;
	unsigned long battery_next_query;
	struct work_struct fn_lock_sync_work;
	bool fn_lock;
};

static int asus_report_battery(struct asus_drvdata *, u8 *, int);

static const struct asus_touchpad_info asus_i2c_tp = {
	.max_x = 2794,
	.max_y = 1758,
	.contact_size = 5,
	.max_contacts = 5,
	.report_size = 28 /* 2 byte header + 5 * 5 + 1 byte footer */,
};

static const struct asus_touchpad_info asus_t100ta_tp = {
	.max_x = 2240,
	.max_y = 1120,
	.res_x = 30, /* units/mm */
	.res_y = 27, /* units/mm */
	.contact_size = 5,
	.max_contacts = 5,
	.report_size = 28 /* 2 byte header + 5 * 5 + 1 byte footer */,
};

static const struct asus_touchpad_info asus_t100ha_tp = {
	.max_x = 2640,
	.max_y = 1320,
	.res_x = 30, /* units/mm */
	.res_y = 29, /* units/mm */
	.contact_size = 5,
	.max_contacts = 5,
	.report_size = 28 /* 2 byte header + 5 * 5 + 1 byte footer */,
};

static const struct asus_touchpad_info asus_t200ta_tp = {
	.max_x = 3120,
	.max_y = 1716,
	.res_x = 30, /* units/mm */
	.res_y = 28, /* units/mm */
	.contact_size = 5,
	.max_contacts = 5,
	.report_size = 28 /* 2 byte header + 5 * 5 + 1 byte footer */,
};

static const struct asus_touchpad_info asus_t100chi_tp = {
	.max_x = 2640,
	.max_y = 1320,
	.res_x = 31, /* units/mm */
	.res_y = 29, /* units/mm */
	.contact_size = 3,
	.max_contacts = 4,
	.report_size = 15 /* 2 byte header + 3 * 4 + 1 byte footer */,
};

static const struct asus_touchpad_info medion_e1239t_tp = {
	.max_x = 2640,
	.max_y = 1380,
	.res_x = 29, /* units/mm */
	.res_y = 28, /* units/mm */
	.contact_size = 5,
	.max_contacts = 5,
	.report_size = 32 /* 2 byte header + 5 * 5 + 5 byte footer */,
};

enum ally_command_codes {
	CMD_SET_GAMEPAD_MODE            = 0x01,
	CMD_SET_MAPPING                 = 0x02,
	CMD_SET_JOYSTICK_MAPPING        = 0x03,
	CMD_SET_JOYSTICK_DEADZONE       = 0x04,
	CMD_SET_TRIGGER_RANGE           = 0x05,
	CMD_SET_VIBRATION_INTENSITY     = 0x06,
	CMD_LED_CONTROL                 = 0x08,
	CMD_CHECK_READY                 = 0x0A,
	CMD_SET_XBOX_CONTROLLER         = 0x0B,
	CMD_CHECK_XBOX_SUPPORT          = 0x0C,
	CMD_USER_CAL_DATA               = 0x0D,
	CMD_CHECK_USER_CAL_SUPPORT      = 0x0E,
	CMD_SET_TURBO_PARAMS            = 0x0F,
	CMD_CHECK_TURBO_SUPPORT         = 0x10,
	CMD_CHECK_RESP_CURVE_SUPPORT    = 0x12,
	CMD_SET_RESP_CURVE              = 0x13,
	CMD_CHECK_DIR_TO_BTN_SUPPORT    = 0x14,
	CMD_SET_GYRO_PARAMS             = 0x15,
	CMD_CHECK_GYRO_TO_JOYSTICK      = 0x16,
	CMD_CHECK_ANTI_DEADZONE         = 0x17,
	CMD_SET_ANTI_DEADZONE           = 0x18,
};

enum ally_gamepad_mode {
	ALLY_GAMEPAD_MODE_GAMEPAD = 0x01,
	ALLY_GAMEPAD_MODE_KEYBOARD = 0x02,
};

static const char *const gamepad_mode_names[] = {
	[ALLY_GAMEPAD_MODE_GAMEPAD] = "gamepad",
	[ALLY_GAMEPAD_MODE_KEYBOARD] = "keyboard"
};

/*
 * XInput rumble magnitudes (report 0x0d, bytes 4-5) use the hardware's
 * 0..100 (0x64) intensity range, matching the Windows XInput capture.
 * The 16-bit evdev magnitude is scaled into this range in ally_x_play_effect().
 */
#define ALLY_FF_MAX_INTENSITY 100

static const u8 ALLY_FORCE_FEEDBACK_OFF[] = {
	0x0D, 0x0F, 0x00, 0x00, 0x00, 0x00, 0xFF, 0x00, 0xEB
};
static_assert(sizeof(struct ff_report) == sizeof(ALLY_FORCE_FEEDBACK_OFF));

/* Changes to ally_drvdata must lock */
static DEFINE_MUTEX(ally_data_mutex);
static struct ally_handheld ally_drvdata = {
	.intf_mutex = __MUTEX_INITIALIZER(ally_drvdata.intf_mutex),
};

static const u8 asus_report_id_init[] = {
	FEATURE_KBD_REPORT_ID,
	FEATURE_KBD_LED_REPORT_ID1,
	FEATURE_KBD_LED_REPORT_ID2
};

static inline int ally_dev_set_report(struct hid_device *hdev, const u8 *buf, size_t len)
{
	u8 *dmabuf __free(kfree) = kmemdup(buf, len, GFP_KERNEL);
	if (!dmabuf)
		return -ENOMEM;

	return hid_hw_raw_request(hdev, buf[0], dmabuf, len,
					HID_FEATURE_REPORT, HID_REQ_SET_REPORT);
}

static inline int ally_dev_get_report(struct hid_device *hdev, u8 *out, size_t len)
{
	return hid_hw_raw_request(hdev, HID_ALLY_GET_REPORT_ID, out, len,
		HID_FEATURE_REPORT, HID_REQ_GET_REPORT);
}

/**
 * handle_ctrl_alt_del() - detect a left button long press.
 * Ally left buton emits a sequence of ctrl+alt+del events:
 * Capture that and emit only a single code for that single event.
 *
 * Return: true iif the event has been managed
 */
static bool handle_ctrl_alt_del(struct hid_device *hdev,
				struct ally_handheld *ally, u8 *data, int size)
{
	bool time_is_past = time_after(jiffies, ally->cad_last_event_time + msecs_to_jiffies(100));

	if (size < 16 || data[0] != 0x01)
		return false;

	if (ally->cad_sequence_state > 0 && time_is_past)
		ally->cad_sequence_state = 0;

	ally->cad_last_event_time = jiffies;

	switch (ally->cad_sequence_state) {
	case 0:
		if (data[1] == 0x01 && data[2] == 0x00 && data[3] == 0x00) {
			ally->cad_sequence_state = 1;
			data[1] = 0x00;
			return true;
		}
		break;
	case 1:
		if (data[1] == 0x05 && data[2] == 0x00 && data[3] == 0x00) {
			ally->cad_sequence_state = 2;
			data[1] = 0x00;
			return true;
		}
		break;
	case 2:
		if (data[1] == 0x05 && data[2] == 0x00 && data[3] == 0x4c) {
			ally->cad_sequence_state = 3;
			data[1] = 0x00;
			data[3] = 0x6F; // F20;
			return true;
		}
		break;
	case 3:
		if (data[1] == 0x04 && data[2] == 0x00 && data[3] == 0x4c) {
			ally->cad_sequence_state = 4;
			data[1] = 0x00;
			data[1] = data[3] = 0x00;
			return true;
		}
		break;
	case 4:
		if (data[1] == 0x00 && data[2] == 0x00 && data[3] == 0x4c) {
			ally->cad_sequence_state = 5;
			data[3] = 0x00;
			return true;
		}
		break;
	}
	ally->cad_sequence_state = 0;
	return false;
}

static bool handle_ally_event(struct hid_device *hdev, u8 *data, int size)
{
	struct input_dev *keyboard_input = ally_drvdata.keyboard_input;
	int keycode = 0;

	if (data[0] == 0x5A) {
		switch (data[1]) {
		case 0x38:
			keycode = KEY_F19;
			break;
		case 0xA6:
			keycode = KEY_F16;
			break;
		case 0xA7:
			keycode = KEY_F17;
			break;
		case 0xA8:
			keycode = KEY_F18;
			break;
		default:
			return false;
		}

		keyboard_input = ally_drvdata.keyboard_input;
		if (keyboard_input && keycode > 0) {
			input_report_key(keyboard_input, keycode, 1);
			input_sync(keyboard_input);
			input_report_key(keyboard_input, keycode, 0);
			input_sync(keyboard_input);
			return true;
		}
	}
	return false;
}

/**
 * ally_gamepad_send_packet() - Send a raw packet to the gamepad device.
 *
 * @ally: ally handheld structure
 * @hdev: hid device
 * @buf: Buffer containing the packet data
 * @len: Length of data to send
 *
 * Return: count of data transferred, negative if error
 */
static int ally_gamepad_send_packet(struct ally_handheld *ally,
			     struct hid_device *hdev, const u8 *buf, size_t len)
{
	scoped_guard(mutex, &ally->intf_mutex) {
		return ally_dev_set_report(hdev, buf, len);
	}
	return -ENODEV;
}

/**
 * ally_gamepad_send_receive_packet() - Send a packet and receive the response.
 * @ally: ally handheld structure
 * @hdev: hid device
 * @buf: Buffer containing the packet data to send and receive response in
 * @len: Length of buffer
 *
 * Return: count of data transferred, negative if error
 */
static int ally_gamepad_send_receive_packet(struct ally_handheld *ally,
					    struct hid_device *hdev,
					    u8 *buf, size_t len)
{
	int ret;

	scoped_guard(mutex, &ally->intf_mutex) {
		ret = ally_dev_set_report(hdev, buf, len);
		if (ret >= 0) {
			memset(buf, 0, len);
			ret = ally_dev_get_report(hdev, buf, len);
		}
	}

	return ret;
}

/**
 * ally_alloc_cmd() - Construct a command buffer for the gamepad
 * @cmd: Command code to send
 * @payload: Optional payload data to include in the command
 * @payload_size: Size of the payload data
 *
 * The constructed buffer is 64 bytes long, and it is the caller
 * responsibility to free the buffer using kfree().
 *
 * Returns the pointer of newly allocated buffer containing the command,
 * or NULL on allocation failure.
 */
static u8 *ally_alloc_cmd(u8 cmd, const u8 *payload, u8 payload_size)
{
	u8 *hidbuf = kzalloc(ROG_ALLY_REPORT_SIZE, GFP_KERNEL);

	if (!hidbuf)
		return NULL;

	hidbuf[0] = HID_ALLY_SET_REPORT_ID;
	hidbuf[1] = HID_ALLY_FEATURE_CODE_PAGE;
	hidbuf[2] = cmd;
	hidbuf[3] = payload_size;

	if (payload_size > 0 && payload)
		memcpy(&hidbuf[4], payload, payload_size);

	return hidbuf;
}

/**
 * ally_check_capability - Check if a specific capability is supported
 * @hdev: HID device
 * @flag_code: Capability flag code to check
 *
 * Returns true if capability is supported, false otherwise
 */
static bool ally_check_capability(struct hid_device *hdev, struct ally_handheld *ally,
				  enum ally_command_codes check_cmd)
{
	u8 payload[] = { 0x00 };
	bool result = false;
	int ret;

	u8 *buf __free(kfree) = ally_alloc_cmd(check_cmd, payload, sizeof(payload));
	if (!buf) {
		hid_err(hdev, "Failed to allocate buffer for capability check.\n");
		goto ally_check_capability_err;
	}

	ret = ally_gamepad_send_receive_packet(ally, hdev, buf, ROG_ALLY_REPORT_SIZE);
	if (ret < 0) {
		hid_err(hdev, "Failed to check capability 0x%02x: %d\n", check_cmd, ret);
		goto ally_check_capability_err;
	}

	if (buf[1] == HID_ALLY_FEATURE_CODE_PAGE && buf[2] == check_cmd)
		result = (buf[4] == 0x01);

ally_check_capability_err:
	return result;
}

static int ally_detect_capabilities(struct hid_device *hdev, struct ally_handheld *ally,
				    struct ally_config *cfg)
{
	if (!hdev || !cfg || !ally)
		return -EINVAL;

	scoped_guard(mutex, &cfg->config_mutex) {
		cfg->is_ally_x = (hdev->product == USB_DEVICE_ID_ASUSTEK_ROG_NKEY_ALLY_X);

		cfg->xbox_controller_support =
			ally_check_capability(hdev, ally, CMD_CHECK_XBOX_SUPPORT);
		cfg->user_cal_support =
			ally_check_capability(hdev, ally, CMD_CHECK_USER_CAL_SUPPORT);
		cfg->turbo_support =
			ally_check_capability(hdev, ally, CMD_CHECK_TURBO_SUPPORT);
		cfg->resp_curve_support =
			ally_check_capability(hdev, ally, CMD_CHECK_RESP_CURVE_SUPPORT);
		cfg->dir_to_btn_support =
			ally_check_capability(hdev, ally, CMD_CHECK_DIR_TO_BTN_SUPPORT);
		cfg->gyro_support =
			ally_check_capability(hdev, ally, CMD_CHECK_GYRO_TO_JOYSTICK);
		cfg->anti_deadzone_support =
			ally_check_capability(hdev, ally, CMD_CHECK_ANTI_DEADZONE);
	}

	return 0;
}

static int ally_set_xbox_controller(struct hid_device *hdev,
				    struct ally_config *cfg, bool enabled)
{
	u8 payload[] = { enabled ? 0x01 : 0x00 };
	int ret;

	if (!cfg || !cfg->xbox_controller_support)
		return -ENODEV;

	u8 *buf __free(kfree) = ally_alloc_cmd(CMD_SET_XBOX_CONTROLLER, payload, sizeof(payload));
	if (!buf)
		return -ENOMEM;

	ret = ally_dev_set_report(hdev, buf, ROG_ALLY_REPORT_SIZE);
	if (ret < 0) {
		hid_err(hdev, "Failed to set Xbox controller mode: %d\n", ret);
		return ret;
	}

	cfg->xbox_controller_enabled = enabled;
	return 0;
}

static ssize_t xbox_controller_show(struct device *dev,
				    struct device_attribute *attr, char *buf)
{
	struct hid_device *hdev = to_hid_device(dev);
	struct asus_drvdata *drvdata = hid_get_drvdata(hdev);
	struct ally_handheld *ally = drvdata->rog_ally;
	struct ally_config *cfg;

	if (!ally || !ally->config)
		return -ENODEV;

	cfg = ally->config;
	if (!cfg->xbox_controller_support)
		return -ENODEV;

	return sprintf(buf, "%d\n", cfg->xbox_controller_enabled);
}

static ssize_t xbox_controller_store(struct device *dev,
				     struct device_attribute *attr,
				     const char *buf, size_t count)
{
	struct hid_device *hdev = to_hid_device(dev);
	struct asus_drvdata *drvdata = hid_get_drvdata(hdev);
	struct ally_handheld *ally = drvdata->rog_ally;
	struct ally_config *cfg;
	bool enabled;
	int ret;

	if (!ally || !ally->config)
		return -ENODEV;

	cfg = ally->config;
	if (!cfg->xbox_controller_support)
		return -ENODEV;

	ret = kstrtobool(buf, &enabled);
	if (ret)
		return ret;

	ret = ally_set_xbox_controller(hdev, cfg, enabled);
	if (ret < 0)
		return ret;

	return count;
}

static DEVICE_ATTR_RW(xbox_controller);

/**
 * ally_set_gamepad_mode - Set the gamepad operating mode
 * @ally: ally handheld structure
 * @hdev: HID device
 * @mode: Gamepad mode to set
 *
 * Returns: 0 on success, negative on failure
 */
static int ally_set_gamepad_mode(struct ally_handheld *ally,
				 struct hid_device *hdev, u8 mode)
{
	struct ally_config *cfg = ally->config;
	u8 payload[] = { mode };
	int ret;

	if (!cfg)
		return -EINVAL;

	if (mode < ALLY_GAMEPAD_MODE_GAMEPAD ||
	    mode > ALLY_GAMEPAD_MODE_KEYBOARD) {
		hid_err(hdev, "Invalid gamepad mode: %u\n", mode);
		return -EINVAL;
	}

	u8 *buf __free(kfree) = ally_alloc_cmd(CMD_SET_GAMEPAD_MODE, payload, sizeof(payload));
	if (!buf)
		return -ENOMEM;

	ret = ally_dev_set_report(hdev, buf, ROG_ALLY_REPORT_SIZE);
	if (ret < 0) {
		hid_err(hdev, "Failed to set gamepad mode: %d\n", ret);
		return ret;
	}

	guard(mutex)(&cfg->config_mutex);
	cfg->gamepad_mode = mode;

	hid_info(hdev, "Set gamepad mode to %s\n", gamepad_mode_names[mode]);
	return 0;
}

static ssize_t gamepad_mode_show(struct device *dev,
				 struct device_attribute *attr, char *buf)
{
	struct hid_device *hdev = to_hid_device(dev);
	struct asus_drvdata *drvdata = hid_get_drvdata(hdev);
	struct ally_handheld *ally = drvdata->rog_ally;
	struct ally_config *cfg;

	if (!ally || !ally->config)
		return -ENODEV;

	cfg = ally->config;

	if (cfg->gamepad_mode >= ALLY_GAMEPAD_MODE_GAMEPAD &&
	    cfg->gamepad_mode <= ALLY_GAMEPAD_MODE_KEYBOARD) {
		return sysfs_emit(buf, "%s\n",
			       gamepad_mode_names[cfg->gamepad_mode]);
	} else {
		return sysfs_emit(buf, "unknown (%u)\n", cfg->gamepad_mode);
	}
}

static ssize_t gamepad_mode_store(struct device *dev,
				  struct device_attribute *attr,
				  const char *buf, size_t count)
{
	struct hid_device *hdev = to_hid_device(dev);
	struct asus_drvdata *drvdata = hid_get_drvdata(hdev);
	struct ally_handheld *ally = drvdata->rog_ally;
	int mode;
	int ret;

	if (!ally || !ally->config)
		return -ENODEV;

	mode = sysfs_match_string(gamepad_mode_names, buf);
	if (mode < 0) {
		hid_err(hdev, "Unknown gamepad mode\n");
		return mode;
	}

	ret = ally_set_gamepad_mode(ally, hdev, mode);
	if (ret < 0)
		return ret;

	return count;
}

static ssize_t gamepad_modes_available_show(struct device *dev,
					    struct device_attribute *attr,
					    char *buf)
{
	int i;
	int len = 0;

	for (i = ALLY_GAMEPAD_MODE_GAMEPAD; i <= ALLY_GAMEPAD_MODE_KEYBOARD;
	     i++) {
		len += sysfs_emit_at(buf, len, "%s ", gamepad_mode_names[i]);
	}

	/* Replace the last space with a newline */
	if (len > 0)
		buf[len - 1] = '\n';

	return len;
}

static DEVICE_ATTR_RW(gamepad_mode);
static DEVICE_ATTR_RO(gamepad_modes_available);

static int ally_set_default_gamepad_mode(struct hid_device *hdev, struct ally_handheld *ally,
					 struct ally_config *cfg)
{
	cfg->gamepad_mode = ALLY_GAMEPAD_MODE_GAMEPAD;

	return ally_set_gamepad_mode(ally, hdev, cfg->gamepad_mode);
}

/**
 * ally_set_vibration_intensity() - Set vibration intensity values
 * @hdev: HID device
 * @cfg: Ally config
 * @left: Left motor intensity (0-100)
 * @right: Right motor intensity (0-100)
 *
 * Returns 0 on success, negative error code on failure
 */
static int ally_set_vibration_intensity(struct hid_device *hdev, struct ally_config *cfg,
					u8 left, u8 right)
{
	u8 payload[] = { left, right };
	int ret;

	u8 *buf __free(kfree) = ally_alloc_cmd(CMD_SET_VIBRATION_INTENSITY, payload, sizeof(payload));
	if (!buf)
		return -ENOMEM;

	ret = ally_dev_set_report(hdev, buf, ROG_ALLY_REPORT_SIZE);
	if (ret < 0) {
		hid_err(hdev, "Failed to set vibration intensity: %d\n", ret);
		return ret;
	}

	return 0;
}

static ssize_t vibration_intensity_left_show(struct device *dev, struct device_attribute *attr,
					char *buf)
{
	struct hid_device *hdev = to_hid_device(dev);
	struct asus_drvdata *drvdata = hid_get_drvdata(hdev);
	struct ally_handheld *ally = drvdata->rog_ally;
	struct ally_config *cfg;

	if (!ally || !ally->config)
		return -ENODEV;

	cfg = ally->config;

	return sprintf(buf, "%u\n", cfg->vibration_intensity_left);
}

static ssize_t vibration_intensity_left_store(struct device *dev, struct device_attribute *attr,
					 const char *buf, size_t count)
{
	struct hid_device *hdev = to_hid_device(dev);
	struct asus_drvdata *drvdata = hid_get_drvdata(hdev);
	struct ally_handheld *ally = drvdata->rog_ally;
	struct ally_config *cfg;
	u8 value;
	int ret;

	if (!ally || !ally->config)
		return -ENODEV;

	cfg = ally->config;

	ret = kstrtou8(buf, 10, &value);
	if (ret || value > 100)
		return -EINVAL;

	ret = ally_set_vibration_intensity(hdev, cfg, value, cfg->vibration_intensity_right);
	if (ret < 0)
		return ret;

	scoped_guard(mutex, &cfg->config_mutex)
		cfg->vibration_intensity_left = value;

	return count;
}

static DEVICE_ATTR_RW(vibration_intensity_left);

static ssize_t vibration_intensity_right_show(struct device *dev, struct device_attribute *attr,
					char *buf)
{
	struct hid_device *hdev = to_hid_device(dev);
	struct asus_drvdata *drvdata = hid_get_drvdata(hdev);
	struct ally_handheld *ally = drvdata->rog_ally;
	struct ally_config *cfg;

	if (!ally || !ally->config)
		return -ENODEV;

	cfg = ally->config;

	return sprintf(buf, "%u\n", cfg->vibration_intensity_right);
}

static ssize_t vibration_intensity_right_store(struct device *dev, struct device_attribute *attr,
					 const char *buf, size_t count)
{
	struct hid_device *hdev = to_hid_device(dev);
	struct asus_drvdata *drvdata = hid_get_drvdata(hdev);
	struct ally_handheld *ally = drvdata->rog_ally;
	struct ally_config *cfg;
	u8 value;
	int ret;

	if (!ally || !ally->config)
		return -ENODEV;

	cfg = ally->config;

	ret = kstrtou8(buf, 10, &value);
	if (ret || value > 100)
		return -EINVAL;

	ret = ally_set_vibration_intensity(hdev, cfg, cfg->vibration_intensity_left, value);
	if (ret < 0)
		return ret;

	scoped_guard(mutex, &cfg->config_mutex)
		cfg->vibration_intensity_right = value;

	return count;
}

static DEVICE_ATTR_RW(vibration_intensity_right);

/**
 * ally_set_joystick_thresholds() - Generic function to set joystick ranges
 *
 * This function send the command to set both inner and outer threshold for
 * the left and right joysticks.
 *
 * @hdev: HID device
 * @left_dz: deadzone of the left stick/trigger (0-255)
 * @left_it: Second parameter
 * @right_it: deadzone of the right stick/trigger (0-255)
 * @right_ot: Fourth parameter
 *
 * Returns 0 on success, negative error code on failure
 */
static int ally_set_joystick_thresholds(struct hid_device *hdev, u8 left_it, u8 left_ot,
					       u8 right_it, u8 right_ot)
{
	u8 payload[] = { left_it, left_ot, right_it, right_ot };
	int ret;

	u8 *buf __free(kfree) = ally_alloc_cmd(CMD_SET_JOYSTICK_DEADZONE, payload, sizeof(payload));
	if (!buf)
		return -ENOMEM;

	ret = ally_dev_set_report(hdev, buf, ROG_ALLY_REPORT_SIZE);
	if (ret < 0) {
		hid_err(hdev, "Failed to set joystick ranges: %d\n", ret);
		return ret;
	}

	return 0;
}

static ssize_t left_joystick_inner_threshold_show(struct device *dev, struct device_attribute *attr,
				      char *buf)
{
	struct hid_device *hdev = to_hid_device(dev);
	struct asus_drvdata *drvdata = hid_get_drvdata(hdev);
	struct ally_handheld *const ally = drvdata->rog_ally;

	if (!ally || !ally->config)
		return -ENODEV;

	return sprintf(buf, "%hhu\n", ally->config->left_deadzone);
}

static ssize_t left_joystick_inner_threshold_store(struct device *dev,
				       struct device_attribute *attr,
				       const char *buf, size_t count)
{
	struct hid_device *hdev = to_hid_device(dev);
	struct asus_drvdata *drvdata = hid_get_drvdata(hdev);
	struct ally_handheld *const ally = drvdata->rog_ally;
	u8 value;
	int ret;

	if (!ally || !ally->config)
		return -ENODEV;

	ret = kstrtou8(buf, 10, &value);
	if (ret || value > 50)
		return -EINVAL;

	ret = ally_set_joystick_thresholds(hdev,
					   value,
					   ally->config->left_outer_threshold,
					   ally->config->right_deadzone,
					   ally->config->right_outer_threshold);
	if (ret)
		return ret;

	scoped_guard(mutex, &ally->config->config_mutex)
		ally->config->left_deadzone = value;

	return count;
}

static DEVICE_ATTR_RW(left_joystick_inner_threshold);

static ssize_t left_joystick_outer_threshold_show(struct device *dev, struct device_attribute *attr,
				      char *buf)
{
	struct hid_device *hdev = to_hid_device(dev);
	struct asus_drvdata *drvdata = hid_get_drvdata(hdev);
	struct ally_handheld *const ally = drvdata->rog_ally;

	if (!ally || !ally->config)
		return -ENODEV;

	return sprintf(buf, "%hhu\n", ally->config->left_outer_threshold);
}

static ssize_t left_joystick_outer_threshold_store(struct device *dev,
				       struct device_attribute *attr,
				       const char *buf, size_t count)
{
	struct hid_device *hdev = to_hid_device(dev);
	struct asus_drvdata *drvdata = hid_get_drvdata(hdev);
	struct ally_handheld *const ally = drvdata->rog_ally;
	u8 value;
	int ret;

	if (!ally || !ally->config)
		return -ENODEV;

	ret = kstrtou8(buf, 10, &value);
	if (ret || value < 70 || value > 100)
		return -EINVAL;

	ret = ally_set_joystick_thresholds(hdev,
					   ally->config->left_deadzone,
					   value,
					   ally->config->right_deadzone,
					   ally->config->right_outer_threshold);
	if (ret)
		return ret;

	scoped_guard(mutex, &ally->config->config_mutex)
		ally->config->left_outer_threshold = value;

	return count;
}

static DEVICE_ATTR_RW(left_joystick_outer_threshold);

static ssize_t right_joystick_inner_threshold_show(struct device *dev, struct device_attribute *attr,
				      char *buf)
{
	struct hid_device *hdev = to_hid_device(dev);
	struct asus_drvdata *drvdata = hid_get_drvdata(hdev);
	struct ally_handheld *const ally = drvdata->rog_ally;

	if (!ally || !ally->config)
		return -ENODEV;

	return sprintf(buf, "%hhu\n", ally->config->right_deadzone);
}

static ssize_t right_joystick_inner_threshold_store(struct device *dev,
				       struct device_attribute *attr,
				       const char *buf, size_t count)
{
	struct hid_device *hdev = to_hid_device(dev);
	struct asus_drvdata *drvdata = hid_get_drvdata(hdev);
	struct ally_handheld *const ally = drvdata->rog_ally;
	u8 value;
	int ret;

	if (!ally || !ally->config)
		return -ENODEV;

	ret = kstrtou8(buf, 10, &value);
	if (ret || value > 50)
		return -EINVAL;

	ret = ally_set_joystick_thresholds(hdev,
					   ally->config->left_deadzone,
					   ally->config->left_outer_threshold,
					   value,
					   ally->config->right_outer_threshold);
	if (ret)
		return ret;

	scoped_guard(mutex, &ally->config->config_mutex)
		ally->config->right_deadzone = value;

	return count;
}

static DEVICE_ATTR_RW(right_joystick_inner_threshold);

static ssize_t right_joystick_outer_threshold_show(struct device *dev, struct device_attribute *attr,
				      char *buf)
{
	struct hid_device *hdev = to_hid_device(dev);
	struct asus_drvdata *drvdata = hid_get_drvdata(hdev);
	struct ally_handheld *const ally = drvdata->rog_ally;

	if (!ally || !ally->config)
		return -ENODEV;

	return sprintf(buf, "%hhu\n", ally->config->right_outer_threshold);
}

static ssize_t right_joystick_outer_threshold_store(struct device *dev,
				       struct device_attribute *attr,
				       const char *buf, size_t count)
{
	struct hid_device *hdev = to_hid_device(dev);
	struct asus_drvdata *drvdata = hid_get_drvdata(hdev);
	struct ally_handheld *const ally = drvdata->rog_ally;
	u8 value;
	int ret;

	if (!ally || !ally->config)
		return -ENODEV;

	ret = kstrtou8(buf, 10, &value);
	if (ret || value < 70 || value > 100)
		return -EINVAL;

	ret = ally_set_joystick_thresholds(hdev,
					   ally->config->left_deadzone,
					   ally->config->left_outer_threshold,
					   ally->config->right_deadzone,
					   value);
	if (ret)
		return ret;

	scoped_guard(mutex, &ally->config->config_mutex)
		ally->config->right_outer_threshold = value;

	return count;
}

static DEVICE_ATTR_RW(right_joystick_outer_threshold);

ALLY_DEVICE_CONST_ATTR_RO(left_joystick_inner_threshold_min, inner_threshold_min, "0\n");
ALLY_DEVICE_CONST_ATTR_RO(left_joystick_inner_threshold_max, inner_threshold_max, "50\n");
ALLY_DEVICE_CONST_ATTR_RO(right_joystick_outer_threshold_min, outer_threshold_min, "70\n");
ALLY_DEVICE_CONST_ATTR_RO(right_joystick_outer_threshold_max, outer_threshold_max, "100\n");

/**
 * ally_set_anti_deadzone - Set anti-deadzone values for joysticks
 * @ally: ally handheld structure
 * @left_adz: Left joystick anti-deadzone value (0-100)
 * @right_adz: Right joystick anti-deadzone value (0-100)
 *
 * Return: 0 on success, negative on failure
 */
static int ally_set_anti_deadzone(struct hid_device *hdev, u8 left_adz, u8 right_adz)
{
	struct asus_drvdata *drvdata = hid_get_drvdata(hdev);
	struct ally_handheld *const ally = drvdata->rog_ally;
	u8 payload[] = { left_adz, right_adz };
	int ret;

	u8 *buf __free(kfree) = ally_alloc_cmd(CMD_SET_ANTI_DEADZONE, payload, sizeof(payload));
	if (!buf)
		return -ENOMEM;

	if (!ally->config->anti_deadzone_support) {
		hid_dbg(hdev, "Anti-deadzone not supported on this device\n");
		return -EOPNOTSUPP;
	}

	ret = ally_dev_set_report(hdev, buf, ROG_ALLY_REPORT_SIZE);
	if (ret < 0) {
		hid_err(hdev, "Failed to set anti-deadzone values: %d\n", ret);
		return ret;
	}

	return 0;
}

static ssize_t left_joystick_anti_deadzone_show(struct device *dev, struct device_attribute *attr,
						char *buf)
{
	struct hid_device *hdev = to_hid_device(dev);
	struct asus_drvdata *drvdata = hid_get_drvdata(hdev);
	struct ally_handheld *const ally = drvdata->rog_ally;

	if (!ally || !ally->config)
		return -ENODEV;

	if (!ally->config->anti_deadzone_support) {
		hid_dbg(hdev, "Anti-deadzone not supported on this device\n");
		return -EOPNOTSUPP;
	}

	return sprintf(buf, "%hhu\n", ally->config->left_anti_deadzone);
}

static ssize_t left_joystick_anti_deadzone_store(struct device *dev, struct device_attribute *attr,
						 const char *buf, size_t count)
{
	struct hid_device *hdev = to_hid_device(dev);
	struct asus_drvdata *drvdata = hid_get_drvdata(hdev);
	struct ally_handheld *const ally = drvdata->rog_ally;
	u8 value;
	int ret;

	if (!ally || !ally->config)
		return -ENODEV;

	if (!ally->config->anti_deadzone_support) {
		hid_dbg(hdev, "Anti-deadzone not supported on this device\n");
		return -EOPNOTSUPP;
	}

	ret = kstrtou8(buf, 10, &value);
	if (ret || value > 100)
		return -EINVAL;

	ret = ally_set_anti_deadzone(hdev, ally->config->left_anti_deadzone, value);
	if (ret)
		return ret;

	scoped_guard(mutex, &ally->config->config_mutex)
		ally->config->left_anti_deadzone = value;

	return count;
}

static DEVICE_ATTR_RW(left_joystick_anti_deadzone);

static ssize_t right_joystick_anti_deadzone_show(struct device *dev, struct device_attribute *attr,
						char *buf)
{
	struct hid_device *hdev = to_hid_device(dev);
	struct asus_drvdata *drvdata = hid_get_drvdata(hdev);
	struct ally_handheld *const ally = drvdata->rog_ally;

	if (!ally || !ally->config)
		return -ENODEV;

	if (!ally->config->anti_deadzone_support) {
		hid_dbg(hdev, "Anti-deadzone not supported on this device\n");
		return -EOPNOTSUPP;
	}

	return sprintf(buf, "%hhu\n", ally->config->right_anti_deadzone);
}

static ssize_t right_joystick_anti_deadzone_store(struct device *dev, struct device_attribute *attr,
						 const char *buf, size_t count)
{
	struct hid_device *hdev = to_hid_device(dev);
	struct asus_drvdata *drvdata = hid_get_drvdata(hdev);
	struct ally_handheld *const ally = drvdata->rog_ally;
	u8 value;
	int ret;

	if (!ally || !ally->config)
		return -ENODEV;

	if (!ally->config->anti_deadzone_support) {
		hid_dbg(hdev, "Anti-deadzone not supported on this device\n");
		return -EOPNOTSUPP;
	}

	ret = kstrtou8(buf, 10, &value);
	if (ret || value > 100)
		return -EINVAL;

	ret = ally_set_anti_deadzone(hdev, value, ally->config->right_anti_deadzone);
	if (ret)
		return ret;

	scoped_guard(mutex, &ally->config->config_mutex)
		ally->config->right_anti_deadzone = value;

	return count;
}

static DEVICE_ATTR_RW(right_joystick_anti_deadzone);

ALLY_DEVICE_CONST_ATTR_RO(left_joystick_anti_deadzone_min, anti_deadzone_min, "0\n");
ALLY_DEVICE_CONST_ATTR_RO(left_joystick_anti_deadzone_max, anti_deadzone_max, "100\n");
ALLY_DEVICE_CONST_ATTR_RO(right_joystick_anti_deadzone_min, anti_deadzone_min, "0\n");
ALLY_DEVICE_CONST_ATTR_RO(right_joystick_anti_deadzone_max, anti_deadzone_max, "100\n");

/**
 * ally_set_trigger_ranges() - Generic function to set triggers ranges
 *
 * This function send the command to set both inner and outer threshold for
 * the left and right triggers.
 *
 * @hdev: HID device
 * @left_dz: deadzone of the left stick/trigger (0-255)
 * @left_it: Second parameter
 * @right_it: deadzone of the right stick/trigger (0-255)
 * @right_ot: Fourth parameter
 *
 * Returns 0 on success, negative error code on failure
 */
static int ally_set_trigger_ranges(struct hid_device *hdev, u8 left_it, u8 left_ot,
					       u8 right_it, u8 right_ot)
{
	const u8 payload[] = { left_it, left_ot, right_it, right_ot };
	int ret;

	u8 *buf __free(kfree) = ally_alloc_cmd(CMD_SET_TRIGGER_RANGE, payload, sizeof(payload));
	if (!buf)
		return -ENOMEM;

	ret = ally_dev_set_report(hdev, buf, ROG_ALLY_REPORT_SIZE);
	if (ret < 0) {
		hid_err(hdev, "Failed to set trigger ranges: %d\n", ret);
		return ret;
	}

	return 0;
}

static ssize_t left_trigger_range_lower_limit_show(struct device *dev, struct device_attribute *attr,
				      char *buf)
{
	struct hid_device *hdev = to_hid_device(dev);
	struct asus_drvdata *drvdata = hid_get_drvdata(hdev);
	struct ally_handheld *const ally = drvdata->rog_ally;

	if (!ally || !ally->config)
		return -ENODEV;

	return sprintf(buf, "%hhu\n", ally->config->left_trigger_min);
}

static ssize_t left_trigger_range_lower_limit_store(struct device *dev,
				       struct device_attribute *attr,
				       const char *buf, size_t count)
{
	struct hid_device *hdev = to_hid_device(dev);
	struct asus_drvdata *drvdata = hid_get_drvdata(hdev);
	struct ally_handheld *const ally = drvdata->rog_ally;
	u8 value;
	int ret;

	if (!ally || !ally->config)
		return -ENODEV;

	ret = kstrtou8(buf, 10, &value);
	if (ret || value > 50)
		return -EINVAL;

	ret = ally_set_trigger_ranges(hdev,
					   value,
					   ally->config->left_trigger_max,
					   ally->config->right_trigger_min,
					   ally->config->right_trigger_max);
	if (ret)
		return ret;

	scoped_guard(mutex, &ally->config->config_mutex)
		ally->config->left_trigger_min = value;

	return count;
}

static DEVICE_ATTR_RW(left_trigger_range_lower_limit);

static ssize_t right_trigger_range_upper_limit_show(struct device *dev, struct device_attribute *attr,
				      char *buf)
{
	struct hid_device *hdev = to_hid_device(dev);
	struct asus_drvdata *drvdata = hid_get_drvdata(hdev);
	struct ally_handheld *const ally = drvdata->rog_ally;

	if (!ally || !ally->config)
		return -ENODEV;

	return sprintf(buf, "%hhu\n", ally->config->right_trigger_max);
}

static ssize_t right_trigger_range_upper_limit_store(struct device *dev,
				       struct device_attribute *attr,
				       const char *buf, size_t count)
{
	struct hid_device *hdev = to_hid_device(dev);
	struct asus_drvdata *drvdata = hid_get_drvdata(hdev);
	struct ally_handheld *const ally = drvdata->rog_ally;
	u8 value;
	int ret;

	if (!ally || !ally->config)
		return -ENODEV;

	ret = kstrtou8(buf, 10, &value);
	if (ret || value < 70 || value > 100)
		return -EINVAL;

	ret = ally_set_trigger_ranges(hdev,
					   ally->config->left_trigger_min,
					   ally->config->left_trigger_max,
					   ally->config->right_trigger_min,
					   value);
	if (ret)
		return ret;

	scoped_guard(mutex, &ally->config->config_mutex)
		ally->config->right_trigger_max = value;

	return count;
}

static DEVICE_ATTR_RW(right_trigger_range_upper_limit);

static ssize_t right_trigger_range_lower_limit_show(struct device *dev, struct device_attribute *attr,
				      char *buf)
{
	struct hid_device *hdev = to_hid_device(dev);
	struct asus_drvdata *drvdata = hid_get_drvdata(hdev);
	struct ally_handheld *const ally = drvdata->rog_ally;

	if (!ally || !ally->config)
		return -ENODEV;

	return sprintf(buf, "%hhu\n", ally->config->right_trigger_min);
}

static ssize_t right_trigger_range_lower_limit_store(struct device *dev,
				       struct device_attribute *attr,
				       const char *buf, size_t count)
{
	struct hid_device *hdev = to_hid_device(dev);
	struct asus_drvdata *drvdata = hid_get_drvdata(hdev);
	struct ally_handheld *const ally = drvdata->rog_ally;
	u8 value;
	int ret;

	if (!ally || !ally->config)
		return -ENODEV;

	ret = kstrtou8(buf, 10, &value);
	if (ret || value > 50)
		return -EINVAL;

	ret = ally_set_trigger_ranges(hdev,
					   ally->config->left_trigger_min,
					   ally->config->left_trigger_max,
					   value,
					   ally->config->right_trigger_max);
	if (ret)
		return ret;

	scoped_guard(mutex, &ally->config->config_mutex)
		ally->config->right_trigger_min = value;

	return count;
}

static DEVICE_ATTR_RW(right_trigger_range_lower_limit);

static ssize_t left_trigger_range_upper_limit_show(struct device *dev, struct device_attribute *attr,
				      char *buf)
{
	struct hid_device *hdev = to_hid_device(dev);
	struct asus_drvdata *drvdata = hid_get_drvdata(hdev);
	struct ally_handheld *const ally = drvdata->rog_ally;

	if (!ally || !ally->config)
		return -ENODEV;

	return sprintf(buf, "%hhu\n", ally->config->left_trigger_max);
}

static ssize_t left_trigger_range_upper_limit_store(struct device *dev,
				       struct device_attribute *attr,
				       const char *buf, size_t count)
{
	struct hid_device *hdev = to_hid_device(dev);
	struct asus_drvdata *drvdata = hid_get_drvdata(hdev);
	struct ally_handheld *const ally = drvdata->rog_ally;
	u8 value;
	int ret;

	if (!ally || !ally->config)
		return -ENODEV;

	ret = kstrtou8(buf, 10, &value);
	if (ret || value < 70 || value > 100)
		return -EINVAL;

	ret = ally_set_trigger_ranges(hdev,
					   ally->config->left_trigger_min,
					   value,
					   ally->config->right_trigger_min,
					   ally->config->right_trigger_max);
	if (ret)
		return ret;

	scoped_guard(mutex, &ally->config->config_mutex)
		ally->config->left_trigger_max = value;

	return count;
}

static DEVICE_ATTR_RW(left_trigger_range_upper_limit);

enum ally_joystick_side {
	JOYSTICK_LEFT = 0,
	JOYSTICK_RIGHT,
};

/**
 * ally_set_joystick_resp_curve - Set joystick response curve parameters
 * @ally: ally handheld structure
 * @hdev: HID device
 * @side: Which joystick side (0=left, 1=right)
 * @curve: Response curve parameter structure
 *
 * Return: 0 on success, negative on failure
 */
static int ally_set_joystick_resp_curve(struct hid_device *hdev, enum ally_joystick_side side,
					struct ally_joystick_resp_curve *curve)
{
	const u8 payload[] = { side,
		curve->entry_1.move, curve->entry_1.resp,
		curve->entry_2.move, curve->entry_2.resp,
		curve->entry_3.move, curve->entry_3.resp,
		curve->entry_4.move, curve->entry_4.resp
	};
	int ret;

	u8 *buf __free(kfree) = ally_alloc_cmd(CMD_SET_RESP_CURVE, payload, sizeof(payload));
	if (!buf)
		return -ENOMEM;

	ret = ally_dev_set_report(hdev, buf, ROG_ALLY_REPORT_SIZE);
	if (ret < 0)
		return ret;

	return 0;
}

static int response_curve_apply(struct hid_device *hdev, bool is_left)
{
	struct asus_drvdata *drvdata = hid_get_drvdata(hdev);
	struct ally_handheld *const ally = drvdata->rog_ally;
	struct ally_config *cfg = ally->config;
	struct ally_joystick_resp_curve *curve;
	int ret;

	curve = is_left ? &cfg->left_curve : &cfg->right_curve;

	if (!(curve->entry_1.move < curve->entry_2.move &&
	      curve->entry_2.move < curve->entry_3.move &&
	      curve->entry_3.move < curve->entry_4.move))
		return -EINVAL;

	ret = ally_set_joystick_resp_curve(hdev,
					    is_left ? JOYSTICK_LEFT : JOYSTICK_RIGHT,
					    curve);
	if (ret) {
		hid_err(hdev, "Failed to set joystick response curve: %d\n", ret);
		return ret;
	}

	return 0;
}

static ssize_t left_response_curve_apply_store(struct device *dev,
					       struct device_attribute *attr,
					       const char *buf, size_t count)
{
	struct hid_device *hdev = to_hid_device(dev);
	struct asus_drvdata *drvdata = hid_get_drvdata(hdev);
	struct ally_handheld *const ally = drvdata->rog_ally;
	bool apply;
	int ret;

	if (!ally || !ally->config)
		return -ENODEV;

	if (!ally->config->resp_curve_support)
		return -EOPNOTSUPP;

	ret = kstrtobool(buf, &apply);
	if (ret)
		return ret;

	if (!apply)
		return count;

	ret = response_curve_apply(hdev, true);
	if (ret < 0)
		return ret;

	return count;
}

static ssize_t right_response_curve_apply_store(struct device *dev,
						struct device_attribute *attr,
						const char *buf, size_t count)
{
	struct hid_device *hdev = to_hid_device(dev);
	struct asus_drvdata *drvdata = hid_get_drvdata(hdev);
	struct ally_handheld *const ally = drvdata->rog_ally;
	bool apply;
	int ret;

	if (!ally || !ally->config)
		return -ENODEV;

	if (!ally->config->resp_curve_support)
		return -EOPNOTSUPP;

	ret = kstrtobool(buf, &apply);
	if (ret)
		return ret;

	if (!apply)
		return count;

	ret = response_curve_apply(hdev, false);
	if (ret < 0)
		return ret;

	return count;
}

static ALLY_DEVICE_ATTR_WO(left_response_curve_apply, response_curve_apply);
static ALLY_DEVICE_ATTR_WO(right_response_curve_apply, response_curve_apply);

static ssize_t response_curve_pct_show(struct device *dev,
				       struct device_attribute *attr, char *buf,
				       struct ally_joystick_resp_curve *curve,
				       int idx)
{
	switch (idx) {
	case 1: return sysfs_emit(buf, "%u\n", curve->entry_1.resp);
	case 2: return sysfs_emit(buf, "%u\n", curve->entry_2.resp);
	case 3: return sysfs_emit(buf, "%u\n", curve->entry_3.resp);
	case 4: return sysfs_emit(buf, "%u\n", curve->entry_4.resp);
	default: return -EINVAL;
	}
}

static ssize_t response_curve_move_show(struct device *dev,
					struct device_attribute *attr, char *buf,
					struct ally_joystick_resp_curve *curve,
					int idx)
{
	switch (idx) {
	case 1: return sysfs_emit(buf, "%u\n", curve->entry_1.move);
	case 2: return sysfs_emit(buf, "%u\n", curve->entry_2.move);
	case 3: return sysfs_emit(buf, "%u\n", curve->entry_3.move);
	case 4: return sysfs_emit(buf, "%u\n", curve->entry_4.move);
	default: return -EINVAL;
	}
}

static ssize_t response_curve_pct_store(struct device *dev,
					struct device_attribute *attr,
					const char *buf, size_t count,
					bool is_left,
					struct ally_handheld *ally, int idx)
{
	struct ally_config *cfg = ally->config;
	struct ally_joystick_resp_curve *curve;
	u8 value;
	int ret;

	if (!cfg->resp_curve_support)
		return -EOPNOTSUPP;

	ret = kstrtou8(buf, 10, &value);
	if (ret)
		return ret;

	if (value > 100)
		return -EINVAL;

	curve = is_left ? &cfg->left_curve : &cfg->right_curve;

	scoped_guard(mutex, &cfg->config_mutex) {
		switch (idx) {
		case 1: curve->entry_1.resp = value; break;
		case 2: curve->entry_2.resp = value; break;
		case 3: curve->entry_3.resp = value; break;
		case 4: curve->entry_4.resp = value; break;
		default: return -EINVAL;
		}
	}

	return count;
}

static ssize_t response_curve_move_store(struct device *dev,
					 struct device_attribute *attr,
					 const char *buf, size_t count,
					 bool is_left,
					 struct ally_handheld *ally, int idx)
{
	struct ally_config *cfg = ally->config;
	struct ally_joystick_resp_curve *curve;
	u8 value;
	int ret;

	if (!cfg->resp_curve_support)
		return -EOPNOTSUPP;

	ret = kstrtou8(buf, 10, &value);
	if (ret)
		return ret;

	if (value > 100)
		return -EINVAL;

	curve = is_left ? &cfg->left_curve : &cfg->right_curve;

	scoped_guard(mutex, &cfg->config_mutex) {
		switch (idx) {
		case 1: curve->entry_1.move = value; break;
		case 2: curve->entry_2.move = value; break;
		case 3: curve->entry_3.move = value; break;
		case 4: curve->entry_4.move = value; break;
		default: return -EINVAL;
		}
	}

	return count;
}

#define DEFINE_JS_CURVE_PCT_FOPS(region, side)					\
	static ssize_t side##_response_curve_pct_##region##_show(		\
		struct device *dev, struct device_attribute *attr, char *buf)	\
	{									\
		struct hid_device *hdev = to_hid_device(dev);			\
		struct asus_drvdata *drvdata = hid_get_drvdata(hdev);		\
		struct ally_handheld *ally = drvdata->rog_ally;			\
		return response_curve_pct_show(					\
			dev, attr, buf, &ally->config->side##_curve, region);\
	}									\
										\
	static ssize_t side##_response_curve_pct_##region##_store(		\
		struct device *dev, struct device_attribute *attr,		\
		const char *buf, size_t count)					\
	{									\
		struct hid_device *hdev = to_hid_device(dev);			\
		struct asus_drvdata *drvdata = hid_get_drvdata(hdev);		\
		struct ally_handheld *ally = drvdata->rog_ally;			\
		return response_curve_pct_store(dev, attr, buf, count,		\
						side##_is_left, ally, region);	\
	}

#define DEFINE_JS_CURVE_MOVE_FOPS(region, side)					\
	static ssize_t side##_response_curve_move_##region##_show(		\
		struct device *dev, struct device_attribute *attr, char *buf)	\
	{									\
		struct hid_device *hdev = to_hid_device(dev);			\
		struct asus_drvdata *drvdata = hid_get_drvdata(hdev);		\
		struct ally_handheld *ally = drvdata->rog_ally;			\
		return response_curve_move_show(					\
			dev, attr, buf, &ally->config->side##_curve, region);\
	}									\
										\
	static ssize_t side##_response_curve_move_##region##_store(		\
		struct device *dev, struct device_attribute *attr,		\
		const char *buf, size_t count)					\
	{									\
		struct hid_device *hdev = to_hid_device(dev);			\
		struct asus_drvdata *drvdata = hid_get_drvdata(hdev);		\
		struct ally_handheld *ally = drvdata->rog_ally;			\
		return response_curve_move_store(dev, attr, buf, count,	\
						 side##_is_left, ally, region);\
	}

#define DEFINE_JS_CURVE_ATTRS(region, side)					\
	DEFINE_JS_CURVE_PCT_FOPS(region, side)					\
	DEFINE_JS_CURVE_MOVE_FOPS(region, side)					\
	static ALLY_DEVICE_ATTR_RW(side##_response_curve_pct_##region,		\
				   response_curve_pct_##region);			\
	static ALLY_DEVICE_ATTR_RW(side##_response_curve_move_##region,		\
				   response_curve_move_##region)

/* Helper defines for "is_left" parameter in DEFINE_JS_CURVE_ATTRS macros */
#define left_is_left true
#define right_is_left false

DEFINE_JS_CURVE_ATTRS(1, left);
DEFINE_JS_CURVE_ATTRS(2, left);
DEFINE_JS_CURVE_ATTRS(3, left);
DEFINE_JS_CURVE_ATTRS(4, left);

DEFINE_JS_CURVE_ATTRS(1, right);
DEFINE_JS_CURVE_ATTRS(2, right);
DEFINE_JS_CURVE_ATTRS(3, right);
DEFINE_JS_CURVE_ATTRS(4, right);

static struct attribute *ally_config_attrs[] = {
	&dev_attr_xbox_controller.attr,
	&dev_attr_vibration_intensity_left.attr,
	&dev_attr_vibration_intensity_right.attr,
	&dev_attr_gamepad_mode.attr,
	&dev_attr_gamepad_modes_available.attr,
	NULL
};

static struct attribute *left_joystick_axis_attrs[] = {
	&dev_attr_left_joystick_inner_threshold.attr,
	&dev_attr_left_joystick_outer_threshold.attr,
	&dev_attr_left_joystick_inner_threshold_min.attr,
	&dev_attr_left_joystick_inner_threshold_max.attr,
	&dev_attr_left_joystick_anti_deadzone.attr,
	&dev_attr_left_joystick_anti_deadzone_min.attr,
	&dev_attr_left_joystick_anti_deadzone_max.attr,
	&dev_attr_left_response_curve_pct_1.attr,
	&dev_attr_left_response_curve_pct_2.attr,
	&dev_attr_left_response_curve_pct_3.attr,
	&dev_attr_left_response_curve_pct_4.attr,
	&dev_attr_left_response_curve_move_1.attr,
	&dev_attr_left_response_curve_move_2.attr,
	&dev_attr_left_response_curve_move_3.attr,
	&dev_attr_left_response_curve_move_4.attr,
	&dev_attr_left_response_curve_apply.attr,
	NULL
};

static struct attribute *right_joystick_axis_attrs[] = {
	&dev_attr_right_joystick_inner_threshold.attr,
	&dev_attr_right_joystick_outer_threshold.attr,
	&dev_attr_right_joystick_outer_threshold_min.attr,
	&dev_attr_right_joystick_outer_threshold_max.attr,
	&dev_attr_right_joystick_anti_deadzone.attr,
	&dev_attr_right_joystick_anti_deadzone_min.attr,
	&dev_attr_right_joystick_anti_deadzone_max.attr,
	&dev_attr_right_response_curve_pct_1.attr,
	&dev_attr_right_response_curve_pct_2.attr,
	&dev_attr_right_response_curve_pct_3.attr,
	&dev_attr_right_response_curve_pct_4.attr,
	&dev_attr_right_response_curve_move_1.attr,
	&dev_attr_right_response_curve_move_2.attr,
	&dev_attr_right_response_curve_move_3.attr,
	&dev_attr_right_response_curve_move_4.attr,
	&dev_attr_right_response_curve_apply.attr,
	NULL
};

static struct attribute *left_trigger_attrs[] = {
	&dev_attr_left_trigger_range_lower_limit.attr,
	&dev_attr_left_trigger_range_upper_limit.attr,
	//&dev_attr_right_joystick_outer_threshold.attr,
	//&dev_attr_right_joystick_outer_threshold_min.attr,
	//&dev_attr_right_joystick_outer_threshold_max.attr,
	NULL
};

static struct attribute *right_trigger_attrs[] = {
	&dev_attr_right_trigger_range_lower_limit.attr,
	&dev_attr_right_trigger_range_upper_limit.attr,
	//&dev_attr_right_joystick_outer_threshold.attr,
	//&dev_attr_right_joystick_outer_threshold_min.attr,
	//&dev_attr_right_joystick_outer_threshold_max.attr,
	NULL
};

static const struct attribute_group ally_attr_groups[] = {
	{
		.attrs = ally_config_attrs,
	},
	{
		.name = "left_joystick_axis",
		.attrs = left_joystick_axis_attrs,
	},
	{
		.name = "right_joystick_axis",
		.attrs = right_joystick_axis_attrs,
	},
	{
		.name = "left_trigger",
		.attrs = left_trigger_attrs,
	},
	{
		.name = "right_trigger",
		.attrs = right_trigger_attrs,
	},
};

/**
 * ally_get_turbo_params - Get turbo parameters for a specific button
 * @cfg: Ally config structure
 * @button_id: Button identifier from ally_button_id enum
 *
 * Returns: Pointer to the button's turbo parameters, or NULL if invalid
 */
static struct ally_btn_turbo_params *ally_get_turbo_params(struct ally_config *cfg,
							   enum ally_button_id button_id)
{
	struct ally_turbo_config *turbo;

	if (!cfg || button_id >= ALLY_BTN_MAX)
		return NULL;

	turbo = &cfg->turbo;

	switch (button_id) {
	case ALLY_BTN_A:
		return &turbo->btn_a;
	case ALLY_BTN_B:
		return &turbo->btn_b;
	case ALLY_BTN_X:
		return &turbo->btn_x;
	case ALLY_BTN_Y:
		return &turbo->btn_y;
	case ALLY_BTN_LB:
		return &turbo->btn_lb;
	case ALLY_BTN_RB:
		return &turbo->btn_rb;
	case ALLY_BTN_DU:
		return &turbo->btn_du;
	case ALLY_BTN_DD:
		return &turbo->btn_dd;
	case ALLY_BTN_DL:
		return &turbo->btn_dl;
	case ALLY_BTN_DR:
		return &turbo->btn_dr;
	case ALLY_BTN_J0B:
		return &turbo->btn_j0b;
	case ALLY_BTN_J1B:
		return &turbo->btn_j1b;
	case ALLY_BTN_MENU:
		return &turbo->btn_menu;
	case ALLY_BTN_VIEW:
		return &turbo->btn_view;
	case ALLY_BTN_M1:
		return &turbo->btn_m1;
	case ALLY_BTN_M2:
		return &turbo->btn_m2;
	default:
		return NULL;
	}
}

/**
 * ally_set_turbo_params - Set turbo parameters for all buttons
 * @hdev: HID device
 * @cfg: Ally config structure
 *
 * Returns: 0 on success, negative on failure
 */
static int ally_set_turbo_params(struct hid_device *hdev, struct ally_config *cfg)
{
	struct asus_drvdata *drvdata = hid_get_drvdata(hdev);
	struct ally_handheld *ally = drvdata->rog_ally;
	struct ally_turbo_config *turbo = &cfg->turbo;

	u8 payload[] = { 
		turbo->btn_du.turbo,
		turbo->btn_du.toggle,
		turbo->btn_dd.turbo,
		turbo->btn_dd.toggle,
		turbo->btn_dl.turbo,
		turbo->btn_dl.toggle,
		turbo->btn_dr.turbo,
		turbo->btn_dr.toggle,
		turbo->btn_j0b.turbo,
		turbo->btn_j0b.toggle,
		turbo->btn_j1b.turbo,
		turbo->btn_j1b.toggle,
		turbo->btn_lb.turbo,
		turbo->btn_lb.toggle,
		turbo->btn_rb.turbo,
		turbo->btn_rb.toggle,
		turbo->btn_a.turbo,
		turbo->btn_a.toggle,
		turbo->btn_b.turbo,
		turbo->btn_b.toggle,
		turbo->btn_x.turbo,
		turbo->btn_x.toggle,
		turbo->btn_y.turbo,
		turbo->btn_y.toggle,
		turbo->btn_view.turbo,
		turbo->btn_view.toggle,
		turbo->btn_menu.turbo,
		turbo->btn_menu.toggle,
		turbo->btn_m2.turbo,
		turbo->btn_m2.toggle,
		turbo->btn_m1.turbo,
		turbo->btn_m1.toggle,
	};
	int ret;

	u8 *buf __free(kfree) = ally_alloc_cmd(CMD_SET_TURBO_PARAMS, payload, sizeof(payload));
	if (!buf)
		return -ENOMEM;

	ret = ally_gamepad_send_packet(ally, hdev, buf, ROG_ALLY_REPORT_SIZE);
	if (ret < 0) {
		hid_err(hdev, "Failed to set turbo parameters: %d\n", ret);
		return ret;
	}

	return 0;
}

struct ally_btn_turbo_attr {
	struct device_attribute dev_attr;
	int button_id;
};

#define to_ally_btn_turbo_attr(x) container_of(x, struct ally_btn_turbo_attr, dev_attr)

static ssize_t ally_btn_turbo_show(struct device *dev,
				   struct device_attribute *attr, char *buf)
{
	struct hid_device *hdev = to_hid_device(dev);
	struct asus_drvdata *drvdata = hid_get_drvdata(hdev);
	struct ally_handheld *ally = drvdata->rog_ally;
	struct ally_btn_turbo_attr *btn_attr = to_ally_btn_turbo_attr(attr);
	struct ally_btn_turbo_params *params;

	if (!ally || !ally->config)
		return -ENODEV;

	if (!ally->config->turbo_support)
		return -EOPNOTSUPP;

	params = ally_get_turbo_params(ally->config, btn_attr->button_id);
	if (!params)
		return -EINVAL;

	/* Format: turbo_interval_ms[,toggle_interval_ms] */
	if (params->toggle)
		return sysfs_emit(buf, "%d,%d\n", params->turbo * 50, params->toggle * 50);

	return sysfs_emit(buf, "%d\n", params->turbo * 50);
}

static ssize_t ally_btn_turbo_store(struct device *dev,
				    struct device_attribute *attr,
				    const char *buf, size_t count)
{
	struct hid_device *hdev = to_hid_device(dev);
	struct asus_drvdata *drvdata = hid_get_drvdata(hdev);
	struct ally_handheld *ally = drvdata->rog_ally;
	struct ally_btn_turbo_attr *btn_attr = to_ally_btn_turbo_attr(attr);
	struct ally_btn_turbo_params *params;
	unsigned int turbo_ms, toggle_ms = 0;
	int ret;

	if (!ally || !ally->config)
		return -ENODEV;

	if (!ally->config->turbo_support)
		return -EOPNOTSUPP;

	params = ally_get_turbo_params(ally->config, btn_attr->button_id);
	if (!params)
		return -EINVAL;

	/* Parse input: turbo_interval_ms[,toggle_interval_ms] */
	ret = sscanf(buf, "%u,%u", &turbo_ms, &toggle_ms);
	if (ret < 1)
		return -EINVAL;

	if (turbo_ms != 0 && (turbo_ms < 50 || turbo_ms > 1000))
		return -EINVAL;

	if (ret > 1 && toggle_ms > 0 && (toggle_ms < 50 || toggle_ms > 1000))
		return -EINVAL;

	scoped_guard(mutex, &ally->config->config_mutex) {
		params->turbo = turbo_ms / 50;
		params->toggle = toggle_ms / 50;

		ret = ally_set_turbo_params(hdev, ally->config);
	}

	if (ret < 0)
		return ret;

	return count;
}

/* Helper to create button turbo attribute */
static struct ally_btn_turbo_attr *ally_btn_turbo_attr_create(int button_id)
{
	struct ally_btn_turbo_attr *attr;

	attr = kzalloc(sizeof(*attr), GFP_KERNEL);
	if (!attr)
		return NULL;

	attr->button_id = button_id;
	sysfs_attr_init(&attr->dev_attr.attr);
	attr->dev_attr.attr.name = "turbo";
	attr->dev_attr.attr.mode = 0644;
	attr->dev_attr.show = ally_btn_turbo_show;
	attr->dev_attr.store = ally_btn_turbo_store;

	return attr;
}

enum btn_map_type {
	BTN_TYPE_NONE = 0,
	BTN_TYPE_PAD = 0x01,
	BTN_TYPE_KB = 0x02,
	BTN_TYPE_MOUSE = 0x03,
	BTN_TYPE_MEDIA = 0x05,
};

struct btn_code_map {
	unsigned char type;
	unsigned char value;
	const char *name;
};

static const struct btn_code_map ally_btn_codes[] = {
	{ BTN_TYPE_NONE, 0x00, "NONE" },
	/* Gamepad button codes */
	{ BTN_TYPE_PAD, 0x01, "PAD_A" },
	{ BTN_TYPE_PAD, 0x02, "PAD_B" },
	{ BTN_TYPE_PAD, 0x03, "PAD_X" },
	{ BTN_TYPE_PAD, 0x04, "PAD_Y" },
	{ BTN_TYPE_PAD, 0x05, "PAD_LB" },
	{ BTN_TYPE_PAD, 0x06, "PAD_RB" },
	{ BTN_TYPE_PAD, 0x07, "PAD_LS" },
	{ BTN_TYPE_PAD, 0x08, "PAD_RS" },
	{ BTN_TYPE_PAD, 0x09, "PAD_DPAD_UP" },
	{ BTN_TYPE_PAD, 0x0A, "PAD_DPAD_DOWN" },
	{ BTN_TYPE_PAD, 0x0B, "PAD_DPAD_LEFT" },
	{ BTN_TYPE_PAD, 0x0C, "PAD_DPAD_RIGHT" },
	{ BTN_TYPE_PAD, 0x0D, "PAD_LT" },
	{ BTN_TYPE_PAD, 0x0E, "PAD_RT" },
	{ BTN_TYPE_PAD, 0x11, "PAD_VIEW" },
	{ BTN_TYPE_PAD, 0x12, "PAD_MENU" },
	{ BTN_TYPE_PAD, 0x13, "PAD_XBOX" },

	/* Keyboard button codes */
	{ BTN_TYPE_KB, 0x8E, "KB_M2" },
	{ BTN_TYPE_KB, 0x8F, "KB_M1" },
	{ BTN_TYPE_KB, 0x76, "KB_ESC" },
	{ BTN_TYPE_KB, 0x50, "KB_F1" },
	{ BTN_TYPE_KB, 0x60, "KB_F2" },
	{ BTN_TYPE_KB, 0x40, "KB_F3" },
	{ BTN_TYPE_KB, 0x0C, "KB_F4" },
	{ BTN_TYPE_KB, 0x03, "KB_F5" },
	{ BTN_TYPE_KB, 0x0B, "KB_F6" },
	{ BTN_TYPE_KB, 0x80, "KB_F7" },
	{ BTN_TYPE_KB, 0x0A, "KB_F8" },
	{ BTN_TYPE_KB, 0x01, "KB_F9" },
	{ BTN_TYPE_KB, 0x09, "KB_F10" },
	{ BTN_TYPE_KB, 0x78, "KB_F11" },
	{ BTN_TYPE_KB, 0x07, "KB_F12" },
	{ BTN_TYPE_KB, 0x18, "KB_F14" },
	{ BTN_TYPE_KB, 0x10, "KB_F15" },
	{ BTN_TYPE_KB, 0x0E, "KB_BACKTICK" },
	{ BTN_TYPE_KB, 0x16, "KB_1" },
	{ BTN_TYPE_KB, 0x1E, "KB_2" },
	{ BTN_TYPE_KB, 0x26, "KB_3" },
	{ BTN_TYPE_KB, 0x25, "KB_4" },
	{ BTN_TYPE_KB, 0x2E, "KB_5" },
	{ BTN_TYPE_KB, 0x36, "KB_6" },
	{ BTN_TYPE_KB, 0x3D, "KB_7" },
	{ BTN_TYPE_KB, 0x3E, "KB_8" },
	{ BTN_TYPE_KB, 0x46, "KB_9" },
	{ BTN_TYPE_KB, 0x45, "KB_0" },
	{ BTN_TYPE_KB, 0x4E, "KB_HYPHEN" },
	{ BTN_TYPE_KB, 0x55, "KB_EQUALS" },
	{ BTN_TYPE_KB, 0x66, "KB_BACKSPACE" },
	{ BTN_TYPE_KB, 0x0D, "KB_TAB" },
	{ BTN_TYPE_KB, 0x15, "KB_Q" },
	{ BTN_TYPE_KB, 0x1D, "KB_W" },
	{ BTN_TYPE_KB, 0x24, "KB_E" },
	{ BTN_TYPE_KB, 0x2D, "KB_R" },
	{ BTN_TYPE_KB, 0x2C, "KB_T" },
	{ BTN_TYPE_KB, 0x35, "KB_Y" },
	{ BTN_TYPE_KB, 0x3C, "KB_U" },
	{ BTN_TYPE_KB, 0x44, "KB_O" },
	{ BTN_TYPE_KB, 0x4D, "KB_P" },
	{ BTN_TYPE_KB, 0x54, "KB_LBRACKET" },
	{ BTN_TYPE_KB, 0x5B, "KB_RBRACKET" },
	{ BTN_TYPE_KB, 0x5D, "KB_BACKSLASH" },
	{ BTN_TYPE_KB, 0x58, "KB_CAPS" },
	{ BTN_TYPE_KB, 0x1C, "KB_A" },
	{ BTN_TYPE_KB, 0x1B, "KB_S" },
	{ BTN_TYPE_KB, 0x23, "KB_D" },
	{ BTN_TYPE_KB, 0x2B, "KB_F" },
	{ BTN_TYPE_KB, 0x34, "KB_G" },
	{ BTN_TYPE_KB, 0x33, "KB_H" },
	{ BTN_TYPE_KB, 0x3B, "KB_J" },
	{ BTN_TYPE_KB, 0x42, "KB_K" },
	{ BTN_TYPE_KB, 0x4B, "KB_L" },
	{ BTN_TYPE_KB, 0x4C, "KB_SEMI" },
	{ BTN_TYPE_KB, 0x52, "KB_QUOTE" },
	{ BTN_TYPE_KB, 0x5A, "KB_RET" },
	{ BTN_TYPE_KB, 0x88, "KB_LSHIFT" },
	{ BTN_TYPE_KB, 0x1A, "KB_Z" },
	{ BTN_TYPE_KB, 0x22, "KB_X" },
	{ BTN_TYPE_KB, 0x21, "KB_C" },
	{ BTN_TYPE_KB, 0x2A, "KB_V" },
	{ BTN_TYPE_KB, 0x32, "KB_B" },
	{ BTN_TYPE_KB, 0x31, "KB_N" },
	{ BTN_TYPE_KB, 0x3A, "KB_M" },
	{ BTN_TYPE_KB, 0x41, "KB_COMMA" },
	{ BTN_TYPE_KB, 0x49, "KB_PERIOD" },
	{ BTN_TYPE_KB, 0x89, "KB_RSHIFT" },
	{ BTN_TYPE_KB, 0x8C, "KB_LCTL" },
	{ BTN_TYPE_KB, 0x82, "KB_META" },
	{ BTN_TYPE_KB, 0x8A, "KB_LALT" },
	{ BTN_TYPE_KB, 0x29, "KB_SPACE" },
	{ BTN_TYPE_KB, 0x8B, "KB_RALT" },
	{ BTN_TYPE_KB, 0x84, "KB_MENU" },
	{ BTN_TYPE_KB, 0x8D, "KB_RCTL" },
	{ BTN_TYPE_KB, 0xC3, "KB_PRNTSCN" },
	{ BTN_TYPE_KB, 0x7E, "KB_SCRLCK" },
	{ BTN_TYPE_KB, 0x91, "KB_PAUSE" },
	{ BTN_TYPE_KB, 0xC2, "KB_INS" },
	{ BTN_TYPE_KB, 0x94, "KB_HOME" },
	{ BTN_TYPE_KB, 0x96, "KB_PGUP" },
	{ BTN_TYPE_KB, 0xC0, "KB_DEL" },
	{ BTN_TYPE_KB, 0x95, "KB_END" },
	{ BTN_TYPE_KB, 0x97, "KB_PGDWN" },
	{ BTN_TYPE_KB, 0x98, "KB_UP_ARROW" },
	{ BTN_TYPE_KB, 0x99, "KB_DOWN_ARROW" },
	{ BTN_TYPE_KB, 0x91, "KB_LEFT_ARROW" },
	{ BTN_TYPE_KB, 0x9B, "KB_RIGHT_ARROW" },

	/* Numpad button codes */
	{ BTN_TYPE_KB, 0x77, "NUMPAD_LOCK" },
	{ BTN_TYPE_KB, 0x90, "NUMPAD_FWDSLASH" },
	{ BTN_TYPE_KB, 0x7C, "NUMPAD_ASTERISK" },
	{ BTN_TYPE_KB, 0x7B, "NUMPAD_HYPHEN" },
	{ BTN_TYPE_KB, 0x70, "NUMPAD_0" },
	{ BTN_TYPE_KB, 0x69, "NUMPAD_1" },
	{ BTN_TYPE_KB, 0x72, "NUMPAD_2" },
	{ BTN_TYPE_KB, 0x7A, "NUMPAD_3" },
	{ BTN_TYPE_KB, 0x6B, "NUMPAD_4" },
	{ BTN_TYPE_KB, 0x73, "NUMPAD_5" },
	{ BTN_TYPE_KB, 0x74, "NUMPAD_6" },
	{ BTN_TYPE_KB, 0x6C, "NUMPAD_7" },
	{ BTN_TYPE_KB, 0x75, "NUMPAD_8" },
	{ BTN_TYPE_KB, 0x7D, "NUMPAD_9" },
	{ BTN_TYPE_KB, 0x79, "NUMPAD_PLUS" },
	{ BTN_TYPE_KB, 0x81, "NUMPAD_ENTER" },
	{ BTN_TYPE_KB, 0x71, "NUMPAD_PERIOD" },

	/* Mouse button codes */
	{ BTN_TYPE_MOUSE, 0x01, "MOUSE_LCLICK" },
	{ BTN_TYPE_MOUSE, 0x02, "MOUSE_RCLICK" },
	{ BTN_TYPE_MOUSE, 0x03, "MOUSE_MCLICK" },
	{ BTN_TYPE_MOUSE, 0x04, "MOUSE_WHEEL_UP" },
	{ BTN_TYPE_MOUSE, 0x05, "MOUSE_WHEEL_DOWN" },

	/* Media button codes */
	{ BTN_TYPE_MEDIA, 0x16, "MEDIA_SCREENSHOT" },
	{ BTN_TYPE_MEDIA, 0x19, "MEDIA_SHOW_KEYBOARD" },
	{ BTN_TYPE_MEDIA, 0x1C, "MEDIA_SHOW_DESKTOP" },
	{ BTN_TYPE_MEDIA, 0x1E, "MEDIA_START_RECORDING" },
	{ BTN_TYPE_MEDIA, 0x01, "MEDIA_MIC_OFF" },
	{ BTN_TYPE_MEDIA, 0x02, "MEDIA_VOL_DOWN" },
	{ BTN_TYPE_MEDIA, 0x03, "MEDIA_VOL_UP" },
};

static const size_t keymap_len = ARRAY_SIZE(ally_btn_codes);

/* Button pair indexes for mapping commands */
enum btn_pair_index {
	BTN_PAIR_DPAD_UPDOWN    = 0x01,
	BTN_PAIR_DPAD_LEFTRIGHT = 0x02,
	BTN_PAIR_STICK_LR       = 0x03,
	BTN_PAIR_BUMPER_LR      = 0x04,
	BTN_PAIR_AB             = 0x05,
	BTN_PAIR_XY             = 0x06,
	BTN_PAIR_VIEW_MENU      = 0x07,
	BTN_PAIR_M1M2           = 0x08,
	BTN_PAIR_TRIGGER_LR     = 0x09,
};

struct button_map {
	struct btn_code_map *remap;
	struct btn_code_map *macro;
};

struct button_pair_map {
	enum btn_pair_index pair_index;
	struct button_map first;
	struct button_map second;
};

/* Store button mapping per gamepad mode */
struct ally_button_mapping {
	struct button_pair_map button_pairs[9]; /* 9 button pairs */
};

/* Find a button code map by its name */
static const struct btn_code_map *find_button_by_name(const char *name)
{
	int i;

	for (i = 0; i < keymap_len; i++) {
		if (strcmp(ally_btn_codes[i].name, name) == 0)
			return &ally_btn_codes[i];
	}

	return NULL;
}

/* Set button mapping for a button pair */
static int ally_set_button_mapping(struct hid_device *hdev, struct ally_handheld *ally,
				  struct button_pair_map *mapping)
{
	if (!mapping)
		return -EINVAL;

	u8 *buf = ally_alloc_cmd(CMD_SET_MAPPING, NULL, 0);
	if (!buf)
		return -ENOMEM;
	
	/* This packet is slightly different from the other
	 * as before the packet length there is an extra byte
	 * which is the pair index.
	 */
	buf[3] = mapping->pair_index;
	buf[4] = 0x2C; /* Length */

	/* First button mapping */
	buf[5] = mapping->first.remap->type;
	/* Fill in bytes 6-14 with button code */
	if (mapping->first.remap->type) {
		unsigned char btn_bytes[10] = {0};
		btn_bytes[0] = mapping->first.remap->type;

		switch (mapping->first.remap->type) {
		case BTN_TYPE_NONE:
			break;
		case BTN_TYPE_PAD:
		case BTN_TYPE_KB:
		case BTN_TYPE_MEDIA:
			btn_bytes[2] = mapping->first.remap->value;
			break;
		case BTN_TYPE_MOUSE:
			btn_bytes[4] = mapping->first.remap->value;
			break;
		}
		memcpy(&buf[5], btn_bytes, 10);
	}

	/* Macro mapping for first button if any */
	buf[15] = mapping->first.macro->type;
	if (mapping->first.macro->type) {
		unsigned char macro_bytes[11] = {0};
		macro_bytes[0] = mapping->first.macro->type;

		switch (mapping->first.macro->type) {
		case BTN_TYPE_NONE:
			break;
		case BTN_TYPE_PAD:
		case BTN_TYPE_KB:
		case BTN_TYPE_MEDIA:
			macro_bytes[2] = mapping->first.macro->value;
			break;
		case BTN_TYPE_MOUSE:
			macro_bytes[4] = mapping->first.macro->value;
			break;
		}
		memcpy(&buf[15], macro_bytes, 11);
	}

	/* Second button mapping */
	buf[27] = mapping->second.remap->type;
	/* Fill in bytes 28-36 with button code */
	if (mapping->second.remap->type) {
		unsigned char btn_bytes[10] = {0};
		btn_bytes[0] = mapping->second.remap->type;

		switch (mapping->second.remap->type) {
		case BTN_TYPE_NONE:
			break;
		case BTN_TYPE_PAD:
		case BTN_TYPE_KB:
		case BTN_TYPE_MEDIA:
			btn_bytes[2] = mapping->second.remap->value;
			break;
		case BTN_TYPE_MOUSE:
			btn_bytes[4] = mapping->second.remap->value;
			break;
		}
		memcpy(&buf[27], btn_bytes, 10);
	}

	/* Macro mapping for second button if any */
	buf[37] = mapping->second.macro->type;
	if (mapping->second.macro->type) {
		unsigned char macro_bytes[11] = {0};
		macro_bytes[0] = mapping->second.macro->type;

		switch (mapping->second.macro->type) {
		case BTN_TYPE_NONE:
			break;
		case BTN_TYPE_PAD:
		case BTN_TYPE_KB:
		case BTN_TYPE_MEDIA:
			macro_bytes[2] = mapping->second.macro->value;
			break;
		case BTN_TYPE_MOUSE:
			macro_bytes[4] = mapping->second.macro->value;
			break;
		}
		memcpy(&buf[37], macro_bytes, 11);
	}

	return ally_gamepad_send_packet(ally, hdev, buf, ROG_ALLY_REPORT_SIZE);
}

/* Button remap attribute structure */
struct button_remap_attr {
	struct device_attribute dev_attr;
	enum ally_button_id button_id;
	bool is_macro;
};

#define to_button_remap_attr(x) container_of(x, struct button_remap_attr, dev_attr)

/* Get appropriate button pair index and position for a given button */
static int get_button_pair_info(enum ally_button_id button_id,
				enum btn_pair_index *pair_idx,
				bool *is_first)
{
	switch (button_id) {
	case ALLY_BTN_DU:
		*pair_idx = BTN_PAIR_DPAD_UPDOWN;
		*is_first = true;
		break;
	case ALLY_BTN_DD:
		*pair_idx = BTN_PAIR_DPAD_UPDOWN;
		*is_first = false;
		break;
	case ALLY_BTN_DL:
		*pair_idx = BTN_PAIR_DPAD_LEFTRIGHT;
		*is_first = true;
		break;
	case ALLY_BTN_DR:
		*pair_idx = BTN_PAIR_DPAD_LEFTRIGHT;
		*is_first = false;
		break;
	case ALLY_BTN_J0B:
		*pair_idx = BTN_PAIR_STICK_LR;
		*is_first = true;
		break;
	case ALLY_BTN_J1B:
		*pair_idx = BTN_PAIR_STICK_LR;
		*is_first = false;
		break;
	case ALLY_BTN_LB:
		*pair_idx = BTN_PAIR_BUMPER_LR;
		*is_first = true;
		break;
	case ALLY_BTN_RB:
		*pair_idx = BTN_PAIR_BUMPER_LR;
		*is_first = false;
		break;
	case ALLY_BTN_A:
		*pair_idx = BTN_PAIR_AB;
		*is_first = true;
		break;
	case ALLY_BTN_B:
		*pair_idx = BTN_PAIR_AB;
		*is_first = false;
		break;
	case ALLY_BTN_X:
		*pair_idx = BTN_PAIR_XY;
		*is_first = true;
		break;
	case ALLY_BTN_Y:
		*pair_idx = BTN_PAIR_XY;
		*is_first = false;
		break;
	case ALLY_BTN_VIEW:
		*pair_idx = BTN_PAIR_VIEW_MENU;
		*is_first = true;
		break;
	case ALLY_BTN_MENU:
		*pair_idx = BTN_PAIR_VIEW_MENU;
		*is_first = false;
		break;
	case ALLY_BTN_M1:
		*pair_idx = BTN_PAIR_M1M2;
		*is_first = true;
		break;
	case ALLY_BTN_M2:
		*pair_idx = BTN_PAIR_M1M2;
		*is_first = false;
		break;
	default:
		return -EINVAL;
	}

	return 0;
}

static ssize_t button_remap_show(struct device *dev,
				 struct device_attribute *attr, char *buf)
{
	struct hid_device *hdev = to_hid_device(dev);
	struct asus_drvdata *drvdata = hid_get_drvdata(hdev);
	struct ally_handheld *ally = drvdata->rog_ally;
	struct button_remap_attr *btn_attr = to_button_remap_attr(attr);
	struct ally_config *cfg;
	enum ally_button_id button_id = btn_attr->button_id;
	enum btn_pair_index pair_idx;
	bool is_first;
	struct button_pair_map *pair;
	struct button_map *btn_map;
	int ret;

	if (!ally || !ally->config)
		return -ENODEV;

	cfg = ally->config;

	ret = get_button_pair_info(button_id, &pair_idx, &is_first);
	if (ret < 0)
		return ret;

	guard(mutex)(&cfg->config_mutex);
	pair = &((struct ally_button_mapping
			  *)(cfg->button_mappings))[cfg->gamepad_mode]
			.button_pairs[pair_idx - 1];
	btn_map = is_first ? &pair->first : &pair->second;

	if (btn_attr->is_macro) {
		if (btn_map->macro->type == BTN_TYPE_NONE)
			return sysfs_emit(buf, "NONE\n");
		else
			return sysfs_emit(buf, "%s\n", btn_map->macro->name);
	} else {
		if (btn_map->remap->type == BTN_TYPE_NONE)
			return sysfs_emit(buf, "NONE\n");
		else
			return sysfs_emit(buf, "%s\n", btn_map->remap->name);
	}
}

static ssize_t button_remap_store(struct device *dev,
				  struct device_attribute *attr,
				  const char *buf, size_t count)
{
	struct hid_device *hdev = to_hid_device(dev);
	struct asus_drvdata *drvdata = hid_get_drvdata(hdev);
	struct ally_handheld *ally = drvdata->rog_ally;
	struct button_remap_attr *btn_attr = to_button_remap_attr(attr);
	struct ally_config *cfg;
	enum ally_button_id button_id = btn_attr->button_id;
	enum btn_pair_index pair_idx;
	bool is_first;
	struct button_pair_map *pair;
	struct button_map *btn_map;
	char btn_name[32];
	const struct btn_code_map *code;
	int ret;

	if (!ally || !ally->config)
		return -ENODEV;

	cfg = ally->config;

	if (sscanf(buf, "%31s", btn_name) != 1)
		return -EINVAL;

	/* Handle "NONE" specially */
	if (strcmp(btn_name, "NONE") == 0) {
		code = &ally_btn_codes[0]; /* NONE entry */
	} else {
		code = find_button_by_name(btn_name);
		if (!code)
			return -EINVAL;
	}

	ret = get_button_pair_info(button_id, &pair_idx, &is_first);
	if (ret < 0)
		return ret;

	scoped_guard(mutex, &cfg->config_mutex) {
		/* Access the mapping for current gamepad mode */
		pair = &((struct ally_button_mapping
				  *)(cfg->button_mappings))[cfg->gamepad_mode]
				.button_pairs[pair_idx - 1];
		btn_map = is_first ? &pair->first : &pair->second;

		if (btn_attr->is_macro) {
			btn_map->macro = (struct btn_code_map *)code;
		} else {
			btn_map->remap = (struct btn_code_map *)code;
		}

		/* Update pair index */
		pair->pair_index = pair_idx;

		/* Send mapping to device */
		ret = ally_set_button_mapping(hdev, ally, pair);
	}

	if (ret < 0)
		return ret;

	return count;
}

/* Helper to create button remap attribute */
static struct button_remap_attr *button_remap_attr_create(enum ally_button_id button_id, bool is_macro)
{
	struct button_remap_attr *attr;

	attr = kzalloc(sizeof(*attr), GFP_KERNEL);
	if (!attr)
		return NULL;

	attr->button_id = button_id;
	attr->is_macro = is_macro;
	sysfs_attr_init(&attr->dev_attr.attr);
	attr->dev_attr.attr.name = is_macro ? "macro" : "remap";
	attr->dev_attr.attr.mode = 0644;
	attr->dev_attr.show = button_remap_show;
	attr->dev_attr.store = button_remap_store;

	return attr;
}

static void ally_set_default_gamepad_mapping(struct ally_button_mapping *mappings)
{
	struct ally_button_mapping *map = &mappings[ALLY_GAMEPAD_MODE_GAMEPAD];
	int i;

	/* Set all pair indexes and initialize to NONE */
	for (i = 0; i < 9; i++) {
		map->button_pairs[i].pair_index = i + 1;
		map->button_pairs[i].first.remap =
			(struct btn_code_map *)&ally_btn_codes[0];
		map->button_pairs[i].first.macro =
			(struct btn_code_map *)&ally_btn_codes[0];
		map->button_pairs[i].second.remap =
			(struct btn_code_map *)&ally_btn_codes[0];
		map->button_pairs[i].second.macro =
			(struct btn_code_map *)&ally_btn_codes[0];
	}

	/* Set direct mappings using array indices */
	map->button_pairs[BTN_PAIR_AB - 1].first.remap =
		(struct btn_code_map *)&ally_btn_codes[1]; /* PAD_A */
	map->button_pairs[BTN_PAIR_AB - 1].second.remap =
		(struct btn_code_map *)&ally_btn_codes[2]; /* PAD_B */

	map->button_pairs[BTN_PAIR_XY - 1].first.remap =
		(struct btn_code_map *)&ally_btn_codes[3]; /* PAD_X */
	map->button_pairs[BTN_PAIR_XY - 1].second.remap =
		(struct btn_code_map *)&ally_btn_codes[4]; /* PAD_Y */

	map->button_pairs[BTN_PAIR_BUMPER_LR - 1].first.remap =
		(struct btn_code_map *)&ally_btn_codes[5]; /* PAD_LB */
	map->button_pairs[BTN_PAIR_BUMPER_LR - 1].second.remap =
		(struct btn_code_map *)&ally_btn_codes[6]; /* PAD_RB */

	map->button_pairs[BTN_PAIR_STICK_LR - 1].first.remap =
		(struct btn_code_map *)&ally_btn_codes[7]; /* PAD_LS */
	map->button_pairs[BTN_PAIR_STICK_LR - 1].second.remap =
		(struct btn_code_map *)&ally_btn_codes[8]; /* PAD_RS */

	map->button_pairs[BTN_PAIR_DPAD_UPDOWN - 1].first.remap =
		(struct btn_code_map *)&ally_btn_codes[9]; /* PAD_DPAD_UP */
	map->button_pairs[BTN_PAIR_DPAD_UPDOWN - 1].second.remap =
		(struct btn_code_map *)&ally_btn_codes[10]; /* PAD_DPAD_DOWN */

	map->button_pairs[BTN_PAIR_DPAD_LEFTRIGHT - 1].first.remap =
		(struct btn_code_map *)&ally_btn_codes[11]; /* PAD_DPAD_LEFT */
	map->button_pairs[BTN_PAIR_DPAD_LEFTRIGHT - 1].second.remap =
		(struct btn_code_map *)&ally_btn_codes[12]; /* PAD_DPAD_RIGHT */

	map->button_pairs[BTN_PAIR_TRIGGER_LR - 1].first.remap =
		(struct btn_code_map *)&ally_btn_codes[13]; /* PAD_LT */
	map->button_pairs[BTN_PAIR_TRIGGER_LR - 1].second.remap =
		(struct btn_code_map *)&ally_btn_codes[14]; /* PAD_RT */

	map->button_pairs[BTN_PAIR_VIEW_MENU - 1].first.remap =
		(struct btn_code_map *)&ally_btn_codes[15]; /* PAD_VIEW */
	map->button_pairs[BTN_PAIR_VIEW_MENU - 1].second.remap =
		(struct btn_code_map *)&ally_btn_codes[16]; /* PAD_MENU */

	map->button_pairs[BTN_PAIR_M1M2 - 1].first.remap =
		(struct btn_code_map *)&ally_btn_codes[19]; /* KB_M1 */
	map->button_pairs[BTN_PAIR_M1M2 - 1].second.remap =
		(struct btn_code_map *)&ally_btn_codes[18]; /* KB_M2 */
}

static void ally_set_default_keyboard_mapping(struct ally_button_mapping *mappings)
{
	struct ally_button_mapping *map = &mappings[ALLY_GAMEPAD_MODE_KEYBOARD];
	int i;

	/* Set all pair indexes and initialize to NONE */
	for (i = 0; i < 9; i++) {
		map->button_pairs[i].pair_index = i + 1;
		map->button_pairs[i].first.remap =
			(struct btn_code_map *)&ally_btn_codes[0];
		map->button_pairs[i].first.macro =
			(struct btn_code_map *)&ally_btn_codes[0];
		map->button_pairs[i].second.remap =
			(struct btn_code_map *)&ally_btn_codes[0];
		map->button_pairs[i].second.macro =
			(struct btn_code_map *)&ally_btn_codes[0];
	}

	/* Set direct mappings using array indices */
	map->button_pairs[BTN_PAIR_AB - 1].first.remap =
		(struct btn_code_map *)&ally_btn_codes[1]; /* PAD_A */
	map->button_pairs[BTN_PAIR_AB - 1].second.remap =
		(struct btn_code_map *)&ally_btn_codes[2]; /* PAD_B */

	map->button_pairs[BTN_PAIR_XY - 1].first.remap =
		(struct btn_code_map *)&ally_btn_codes[3]; /* PAD_X */
	map->button_pairs[BTN_PAIR_XY - 1].second.remap =
		(struct btn_code_map *)&ally_btn_codes[4]; /* PAD_Y */

	map->button_pairs[BTN_PAIR_BUMPER_LR - 1].first.remap =
		(struct btn_code_map *)&ally_btn_codes[5]; /* PAD_LB */
	map->button_pairs[BTN_PAIR_BUMPER_LR - 1].second.remap =
		(struct btn_code_map *)&ally_btn_codes[6]; /* PAD_RB */

	map->button_pairs[BTN_PAIR_STICK_LR - 1].first.remap =
		(struct btn_code_map *)&ally_btn_codes[7]; /* PAD_LS */
	map->button_pairs[BTN_PAIR_STICK_LR - 1].second.remap =
		(struct btn_code_map *)&ally_btn_codes[8]; /* PAD_RS */

	map->button_pairs[BTN_PAIR_DPAD_UPDOWN - 1].first.remap =
		(struct btn_code_map *)&ally_btn_codes[9]; /* PAD_DPAD_UP */
	map->button_pairs[BTN_PAIR_DPAD_UPDOWN - 1].second.remap =
		(struct btn_code_map *)&ally_btn_codes[10]; /* PAD_DPAD_DOWN */

	map->button_pairs[BTN_PAIR_DPAD_LEFTRIGHT - 1].first.remap =
		(struct btn_code_map *)&ally_btn_codes[11]; /* PAD_DPAD_LEFT */
	map->button_pairs[BTN_PAIR_DPAD_LEFTRIGHT - 1].second.remap =
		(struct btn_code_map *)&ally_btn_codes[12]; /* PAD_DPAD_RIGHT */

	map->button_pairs[BTN_PAIR_TRIGGER_LR - 1].first.remap =
		(struct btn_code_map *)&ally_btn_codes[13]; /* PAD_LT */
	map->button_pairs[BTN_PAIR_TRIGGER_LR - 1].second.remap =
		(struct btn_code_map *)&ally_btn_codes[14]; /* PAD_RT */

	map->button_pairs[BTN_PAIR_VIEW_MENU - 1].first.remap =
		(struct btn_code_map *)&ally_btn_codes[15]; /* PAD_VIEW */
	map->button_pairs[BTN_PAIR_VIEW_MENU - 1].second.remap =
		(struct btn_code_map *)&ally_btn_codes[16]; /* PAD_MENU */

	map->button_pairs[BTN_PAIR_M1M2 - 1].first.remap =
		(struct btn_code_map *)&ally_btn_codes[19]; /* KB_M1 */
	map->button_pairs[BTN_PAIR_M1M2 - 1].second.remap =
		(struct btn_code_map *)&ally_btn_codes[18]; /* KB_M2 */
}

/* Structure to hold button sysfs information */
struct ally_btn_sysfs_entry {
	struct attribute_group group;
	struct attribute *attrs[4]; /* turbo + remap + macro + NULL terminator */
	struct ally_btn_turbo_attr *turbo_attr;
	struct button_remap_attr *remap_attr;
	struct button_remap_attr *macro_attr;
};

/**
 * ally_create_button_attributes - Create turbo button attributes
 * @hdev: HID device
 * @cfg: Ally config structure
 *
 * Returns: 0 on success, negative on failure
 */
static int ally_create_button_attributes(struct hid_device *hdev, struct ally_config *cfg)
{
	struct ally_btn_sysfs_entry *entries;
	int i, ret;
	struct ally_button_mapping *mappings;

	entries = devm_kcalloc(&hdev->dev, ALLY_BTN_MAX, sizeof(*entries), GFP_KERNEL);
	if (!entries)
		return -ENOMEM;

	/* Allocate mappings for each gamepad mode (1-based indexing) */
	mappings = devm_kcalloc(&hdev->dev, ALLY_GAMEPAD_MODE_KEYBOARD + 1,
				sizeof(*mappings), GFP_KERNEL);
	if (!mappings) {
		ret = -ENOMEM;
		goto err_free_entries;
	}

	cfg->button_entries = entries;
	cfg->button_mappings = mappings;
	ally_set_default_gamepad_mapping(mappings);
	ally_set_default_keyboard_mapping(mappings);

	for (i = 0; i < ALLY_BTN_MAX; i++) {
		if (cfg->turbo_support) {
			entries[i].turbo_attr = ally_btn_turbo_attr_create(i);
			if (!entries[i].turbo_attr) {
				ret = -ENOMEM;
				goto err_cleanup;
			}
		}

		entries[i].remap_attr = button_remap_attr_create(i, false);
		if (!entries[i].remap_attr) {
			ret = -ENOMEM;
			goto err_cleanup;
		}

		entries[i].macro_attr = button_remap_attr_create(i, true);
		if (!entries[i].macro_attr) {
			ret = -ENOMEM;
			goto err_cleanup;
		}

		/* Set up attributes array based on what's supported */
		if (cfg->turbo_support) {
			entries[i].attrs[0] =
				&entries[i].turbo_attr->dev_attr.attr;
			entries[i].attrs[1] =
				&entries[i].remap_attr->dev_attr.attr;
			entries[i].attrs[2] =
				&entries[i].macro_attr->dev_attr.attr;
			entries[i].attrs[3] = NULL;
		} else {
			entries[i].attrs[0] =
				&entries[i].remap_attr->dev_attr.attr;
			entries[i].attrs[1] =
				&entries[i].macro_attr->dev_attr.attr;
			entries[i].attrs[2] = NULL;
		}

		entries[i].group.name = ally_button_names[i];
		entries[i].group.attrs = entries[i].attrs;

		ret = sysfs_create_group(&hdev->dev.kobj, &entries[i].group);
		if (ret < 0) {
			hid_err(hdev,
				"Failed to create sysfs group for %s: %d\n",
				ally_button_names[i], ret);
			goto err_cleanup;
		}
	}

	return 0;

err_cleanup:
	while (--i >= 0) {
		sysfs_remove_group(&hdev->dev.kobj, &entries[i].group);
		if (entries[i].turbo_attr)
			kfree(entries[i].turbo_attr);
		if (entries[i].remap_attr)
			kfree(entries[i].remap_attr);
		if (entries[i].macro_attr)
			kfree(entries[i].macro_attr);
	}

err_free_entries:
	if (mappings)
		devm_kfree(&hdev->dev, mappings);
	devm_kfree(&hdev->dev, entries);
	/* Nullify the entries and mappings to prevent use-after-free crashes */
	cfg->button_entries = NULL;
	cfg->button_mappings = NULL;
	return ret;
}

/**
 * ally_remove_button_attributes - Remove turbo button attributes
 * @hdev: HID device
 * @cfg: Ally config structure
 */
static void ally_remove_button_attributes(struct hid_device *hdev, struct ally_config *cfg)
{
	struct ally_btn_sysfs_entry *entries;
	int i;

	if (!cfg || !cfg->button_entries)
		return;

	entries = cfg->button_entries;

	for (i = 0; i < ALLY_BTN_MAX; i++) {
		sysfs_remove_group(&hdev->dev.kobj, &entries[i].group);
		if (entries[i].turbo_attr)
			kfree(entries[i].turbo_attr);
		if (entries[i].remap_attr)
			kfree(entries[i].remap_attr);
		if (entries[i].macro_attr)
			kfree(entries[i].macro_attr);
	}

	if (cfg->button_mappings)
		devm_kfree(&hdev->dev, cfg->button_mappings);
	devm_kfree(&hdev->dev, entries);
}

/**
 * ally_config_create() - Initialize configuration and create sysfs entries
 * @hdev: HID device
 * @ally: Non-NULL ally device data with uninitialized config pointer
 *
 * Returns valid pointer on success, error pointer on failure.
 */
static struct ally_config *ally_config_create(struct hid_device *hdev, struct ally_handheld *ally)
{
	struct ally_config *cfg;
	int ret, sysfs_i;

	cfg = devm_kzalloc(&hdev->dev, sizeof(*cfg), GFP_KERNEL);
	if (!cfg)
		return ERR_PTR(-ENOMEM);

	ret = ally_detect_capabilities(hdev, ally, cfg);
	if (ret < 0) {
		hid_err(hdev, "Failed to detect Ally capabilities: %d\n", ret);
		goto ally_config_create_err;
	}

	for (sysfs_i = 0; sysfs_i < ARRAY_SIZE(ally_attr_groups); sysfs_i++) {
		ret = devm_device_add_group(&hdev->dev, &ally_attr_groups[sysfs_i]);
		if (ret < 0) {
			hid_err(hdev, "Failed to create sysfs group '%s': %d\n",
				ally_attr_groups[sysfs_i].name, ret);
			goto ally_config_create_sysfs_err;
		}
	}

	if (cfg->turbo_support) {
		ret = ally_create_button_attributes(hdev, cfg);
		if (ret < 0) {
			hid_err(hdev, "Failed to create button attributes: %d\n", ret);
			goto ally_config_create_sysfs_err;
		}
	}

	cfg->gamepad_mode = 0x01;
	cfg->left_deadzone = 10;
	cfg->left_outer_threshold = 90;
	cfg->right_deadzone = 10;
	cfg->right_outer_threshold = 90;
	cfg->vibration_intensity_left = 100;
	cfg->vibration_intensity_right = 100;
	cfg->vibration_active = false;

	/* Initialize default response curve values (linear) */
	cfg->left_curve.entry_1.move = 0;
	cfg->left_curve.entry_1.resp = 0;
	cfg->left_curve.entry_2.move = 33;
	cfg->left_curve.entry_2.resp = 33;
	cfg->left_curve.entry_3.move = 66;
	cfg->left_curve.entry_3.resp = 66;
	cfg->left_curve.entry_4.move = 100;
	cfg->left_curve.entry_4.resp = 100;

	cfg->right_curve.entry_1.move = 0;
	cfg->right_curve.entry_1.resp = 0;
	cfg->right_curve.entry_2.move = 33;
	cfg->right_curve.entry_2.resp = 33;
	cfg->right_curve.entry_3.move = 66;
	cfg->right_curve.entry_3.resp = 66;
	cfg->right_curve.entry_4.move = 100;
	cfg->right_curve.entry_4.resp = 100;

	/* So far the only hardware this is supported is the Ally 1 */
	if (cfg->xbox_controller_support) {
		ret = ally_set_xbox_controller(hdev, cfg, true);
		if (ret < 0)
			hid_warn(hdev, "Failed to set default Xbox controller mode: %d\n",
				ret);
	}

	cfg->initialized = true;

	return cfg;
ally_config_create_sysfs_err:
	if (cfg->turbo_support && cfg->button_entries)
		ally_remove_button_attributes(hdev, cfg);
ally_config_create_err:
	ally->config = NULL;
	devm_kfree(&hdev->dev, cfg);
	return ERR_PTR(ret);
}

/**
 * ally_config_remove() - Clean up configuration resources
 * @hdev: HID device
 * @ally: Non-NULL Ally device data
 */
static void ally_config_remove(struct hid_device *hdev, struct ally_handheld *ally)
{
	struct ally_config *cfg = ally->config;

	if (!cfg || !cfg->initialized)
		return;

	if (cfg->turbo_support && cfg->button_entries)
		ally_remove_button_attributes(hdev, cfg);
}

/*
 * This should be called before any remapping attempts,
 * and on driver init/resume, after the asus handshake
 * has been performed on the configuration endpoint.
 */
static int ally_gamepad_check_ready(struct hid_device *hdev)
{
	u8 payload[] = { 0x00 };
	int ret = -ENODEV;

	for (int i = 0; i < HID_ALLY_READY_MAX_TRIES; i++) {
		u8 *buf __free(kfree) = ally_alloc_cmd(CMD_CHECK_READY, payload, sizeof(payload));
		if (!buf)
			return -ENOMEM;

		ret = ally_gamepad_send_receive_packet(&ally_drvdata, hdev, buf, ROG_ALLY_REPORT_SIZE);
		if (ret < 0) {
			hid_dbg(hdev, "ROG Ally check %d/%d failed: %d\n", i,
				 HID_ALLY_READY_MAX_TRIES, ret);
			continue;
		}

		if (buf[2] == CMD_CHECK_READY)
			return 0;

		usleep_range(1000, 2000);
	}

	hid_err(hdev, "ROG Ally never responded with a ready\n");
	return ret;
}

static u8 ally_get_endpoint_address(struct hid_device *hdev)
{
	struct usb_host_endpoint *ep;
	struct usb_interface *intf;

	intf = to_usb_interface(hdev->dev.parent);
	if (!intf || !intf->cur_altsetting)
		return -ENODEV;

	ep = intf->cur_altsetting->endpoint;
	if (!ep)
		return -ENODEV;

	return ep->desc.bEndpointAddress;
}

struct ally_x_input_report {
	uint16_t x, y;
	uint16_t rx, ry;
	uint16_t z, rz;
	uint8_t buttons[4];
} __packed;

/* The hatswitch outputs integers, we use them to index this X|Y pair */
static const int hat_values[][2] = {
	{ 0, 0 }, { 0, -1 }, { 1, -1 }, { 1, 0 },   { 1, 1 },
	{ 0, 1 }, { -1, 1 }, { -1, 0 }, { -1, -1 },
};

/* Return true if event was handled, otherwise false */
static bool ally_x_raw_event(struct input_dev *input, struct hid_device *hdev,
			    struct hid_report *report, u8 *data, int size)
{
	struct ally_x_input_report *in_report;
	u8 byte;

	if (!input)
		return false;

	if (data[0] == 0x0B) {
		in_report = (struct ally_x_input_report *)&data[1];

		input_report_abs(input, ABS_X, in_report->x - 32768);
		input_report_abs(input, ABS_Y, in_report->y - 32768);
		input_report_abs(input, ABS_RX, in_report->rx - 32768);
		input_report_abs(input, ABS_RY, in_report->ry - 32768);
		input_report_abs(input, ABS_Z, in_report->z);
		input_report_abs(input, ABS_RZ, in_report->rz);

		byte = in_report->buttons[0];
		input_report_key(input, BTN_A, byte & BIT(0));
		input_report_key(input, BTN_B, byte & BIT(1));
		input_report_key(input, BTN_X, byte & BIT(2));
		input_report_key(input, BTN_Y, byte & BIT(3));
		input_report_key(input, BTN_TL, byte & BIT(4));
		input_report_key(input, BTN_TR, byte & BIT(5));
		input_report_key(input, BTN_SELECT, byte & BIT(6));
		input_report_key(input, BTN_START, byte & BIT(7));

		byte = in_report->buttons[1];
		input_report_key(input, BTN_THUMBL, byte & BIT(0));
		input_report_key(input, BTN_THUMBR, byte & BIT(1));
		input_report_key(input, BTN_MODE, byte & BIT(2));

		byte = in_report->buttons[2];
		input_report_abs(input, ABS_HAT0X, hat_values[byte][0]);
		input_report_abs(input, ABS_HAT0Y, hat_values[byte][1]);

		input_sync(input);

		return true;
	}

	return false;
}

static void ally_x_ff_work_fn(struct work_struct *work)
{
	struct ally_handheld *ally =
		container_of(work, struct ally_handheld, ff_work);
	struct ff_report report;
	bool update = false;
	int ret;

	scoped_guard(spinlock_irqsave, &ally->ff_lock) {
		if (ally->update_ff) {
			report = ally->ff_packet;
			ally->update_ff = false;
			update = true;
		}
	}

	if (!update || !ally->ally_x_hdev)
		return;

	ret = ally_gamepad_send_packet(ally, ally->ally_x_hdev,
				       (u8 *)&report, sizeof(report));
	if (ret < 0)
		hid_err(ally->ally_x_hdev, "Failed to send force-feedback: %d\n", ret);
}

static int ally_x_play_effect(struct input_dev *idev, void *data,
			      struct ff_effect *effect)
{
	struct ally_handheld *ally = &ally_drvdata;

	if (effect->type != FF_RUMBLE)
		return 0;

	scoped_guard(spinlock_irqsave, &ally->ff_lock) {
		ally->ff_packet.ff.magnitude_strong =
			effect->u.rumble.strong_magnitude * ALLY_FF_MAX_INTENSITY / 65535;
		ally->ff_packet.ff.magnitude_weak =
			effect->u.rumble.weak_magnitude * ALLY_FF_MAX_INTENSITY / 65535;
		ally->update_ff = true;
	}

	if (ally->ff_work_initialized)
		schedule_work(&ally->ff_work);

	return 0;
}

static struct input_dev *ally_x_alloc_input_dev(struct hid_device *hdev,
						const char *name_suffix)
{
	struct input_dev *input_dev = devm_input_allocate_device(&hdev->dev);

	if (!input_dev)
		return ERR_PTR(-ENOMEM);

	input_dev->id.bustype = hdev->bus;
	input_dev->id.vendor = hdev->vendor;
	input_dev->id.product = hdev->product;
	input_dev->id.version = hdev->version;
	input_dev->uniq = hdev->uniq;
	input_dev->name = "ASUS ROG Ally X Gamepad";

	input_set_drvdata(input_dev, hdev);

	return input_dev;
}

static int ally_x_setup_input(struct hid_device *hdev, struct ally_handheld *ally)
{
	struct input_dev *input = ally_x_alloc_input_dev(hdev, NULL);
	int ret;

	if (IS_ERR(input))
		return PTR_ERR(input);

	input_set_abs_params(input, ABS_X, -32768, 32767, 0, 0);
	input_set_abs_params(input, ABS_Y, -32768, 32767, 0, 0);
	input_set_abs_params(input, ABS_RX, -32768, 32767, 0, 0);
	input_set_abs_params(input, ABS_RY, -32768, 32767, 0, 0);
	input_set_abs_params(input, ABS_Z, 0, 1023, 0, 0);
	input_set_abs_params(input, ABS_RZ, 0, 1023, 0, 0);
	input_set_abs_params(input, ABS_HAT0X, -1, 1, 0, 0);
	input_set_abs_params(input, ABS_HAT0Y, -1, 1, 0, 0);
	input_set_capability(input, EV_KEY, BTN_A);
	input_set_capability(input, EV_KEY, BTN_B);
	input_set_capability(input, EV_KEY, BTN_X);
	input_set_capability(input, EV_KEY, BTN_Y);
	input_set_capability(input, EV_KEY, BTN_TL);
	input_set_capability(input, EV_KEY, BTN_TR);
	input_set_capability(input, EV_KEY, BTN_SELECT);
	input_set_capability(input, EV_KEY, BTN_START);
	input_set_capability(input, EV_KEY, BTN_MODE);
	input_set_capability(input, EV_KEY, BTN_THUMBL);
	input_set_capability(input, EV_KEY, BTN_THUMBR);

	input_set_capability(input, EV_KEY, KEY_PROG1);
	input_set_capability(input, EV_KEY, KEY_F16);
	input_set_capability(input, EV_KEY, KEY_F17);
	input_set_capability(input, EV_KEY, KEY_F18);
	input_set_capability(input, EV_KEY, BTN_TRIGGER_HAPPY);
	input_set_capability(input, EV_KEY, BTN_TRIGGER_HAPPY1);

	memcpy(&ally->ff_packet, ALLY_FORCE_FEEDBACK_OFF, sizeof(ally->ff_packet));
	spin_lock_init(&ally->ff_lock);
	INIT_WORK(&ally->ff_work, ally_x_ff_work_fn);
	ally->ff_work_initialized = true;

	input_set_capability(input, EV_FF, FF_RUMBLE);
	ret = input_ff_create_memless(input, NULL, ally_x_play_effect);
	if (ret)
		hid_warn(hdev, "Failed to create force-feedback: %d\n", ret);

	ret = input_register_device(input);
	if (ret) {
		hid_err(hdev, "Failed to register Ally X gamepad device: %d\n", ret);
		goto ally_x_setup_input_err;
	}

	ally->ally_x_input = input;

	return 0;
ally_x_setup_input_err:
	return ret;
}

static int hid_asus_ally_init(struct hid_device *hdev)
{
	int ret;

	/*
	 * This function assumes the asus-specific initialization
	 * to have been performed already at this point.
	 */
	ret = ally_gamepad_check_ready(hdev);
	if (ret < 0) {
		hid_err(hdev, "ROG Ally device is not ready: %d\n", ret);
		return ret;
	}

	/* Failure at this point is non-critical */
	ret = ally_gamepad_send_packet(&ally_drvdata, hdev, ALLY_FORCE_FEEDBACK_OFF,
				       sizeof(ALLY_FORCE_FEEDBACK_OFF));
	if (ret < 0)
		hid_err(hdev, "Ally failed to init force-feedback off: %d\n", ret);

	/* Set the default gamepad mode now that the MCU is confirmed ready */
	if (ally_drvdata.config) {
		ret = ally_set_default_gamepad_mode(hdev, &ally_drvdata, ally_drvdata.config);
		if (ret < 0)
			hid_warn(hdev, "Failed to set default gamepad mode: %d\n", ret);
	}

	return 0;
}

static bool hid_asus_ally_raw_event(struct hid_device *hdev, struct ally_handheld *ally,
			struct hid_report *report, u8 *data, int size)
{
	if (!ally)
		return false;

	switch (ally_get_endpoint_address(hdev)) {
	case HID_ALLY_X_INTF_IN:
		if (ally_x_raw_event(ally_drvdata.ally_x_input,
				     ally_drvdata.ally_x_hdev, report,
				     data, size))
			return true;
		break;
	case HID_ALLY_INTF_CFG_IN:
		if (handle_ally_event(hdev, data, size))
			return true;
		break;
	case HID_ALLY_INTF_KEYBOARD_IN:
		if (handle_ctrl_alt_del(hdev, ally, data, size))
			return true;
		break;
	default:
		break;
	}

	return false;
}

/******************************************************************************/
/* ROG Ally LED ring control                                                  */
/******************************************************************************/

/*
 * The ROG Ally LED controller supports 4 discrete brightness levels (0-3).
 * Aura animations (Rainbow/Color Cycle) ignore R/G/B bytes and only
 * respond to this global brightness command.
 */
static int ally_rgb_apply_brightness(struct ally_rgb_dev *led_rgb)
{
	int br = led_rgb->led_rgb_dev.led_cdev.brightness;
	u8 buf[] = { FEATURE_KBD_LED_REPORT_ID1, ALLY_LED_BRIGHTNESS_CMD1,
		     ALLY_LED_BRIGHTNESS_CMD2, ALLY_LED_BRIGHTNESS_CMD3, 0x00 };
	u8 level;
	int ret;

	if (ally_drvdata.led_rgb_data.mode == ALLY_RGB_EFFECT_STATIC) {
		/* Dimming in static mode uses software scaling; hardware level fixed at max. */
		level = 3;
	} else {
		/* Map 0-100 to 0-3 hardware levels for animations */
		if (br == 0 || !ally_drvdata.led_rgb_data.enabled)
			level = 0;
		else if (br <= 33)
			level = 1;
		else if (br <= 66)
			level = 2;
		else
			level = 3;
	}

	/* Send brightness report only on level changes; may be NV-backed. */
	if (level == led_rgb->last_hw_level)
		return 0;

	buf[4] = level;

	ret = ally_dev_set_report(led_rgb->hdev, buf, sizeof(buf));
	if (ret >= 0)
		led_rgb->last_hw_level = level;

	return ret;
}

static int ally_rgb_apply_effect(struct ally_rgb_dev *led_rgb)
{
	static const u8 speed_lut[] = {
		ALLY_LED_SPEED_SLOW, ALLY_LED_SPEED_MED, ALLY_LED_SPEED_FAST
	};
	struct ally_rgb_report rpt = {
		.report_id = FEATURE_KBD_REPORT_ID,
		.cmd = ALLY_LED_CMD_CONFIG,
		.zone = 0x00,
		.effect = ally_rgb_effect_wire[ally_drvdata.led_rgb_data.mode],
		/*
		 * Unscaled intensity, not the scaled .brightness: animation
		 * dimming is the 4-level 0x5d hardware control, so scaled
		 * values here would dim twice.
		 */
		.red = led_rgb->led_rgb_dev.subled_info[0].intensity,
		.green = led_rgb->led_rgb_dev.subled_info[1].intensity,
		.blue = led_rgb->led_rgb_dev.subled_info[2].intensity,
	};
	u8 set_buf[ROG_ALLY_REPORT_SIZE] = {
		FEATURE_KBD_REPORT_ID, ALLY_LED_CMD_SET
	};
	u8 buf[ROG_ALLY_REPORT_SIZE] = {};
	int ret;

	if (!led_rgb || !led_rgb->hdev)
		return -ENODEV;

	if (ally_drvdata.led_rgb_data.mode != ALLY_RGB_EFFECT_STATIC) {
		u8 idx;

		if (ally_drvdata.led_rgb_data.speed <= 33)
			idx = 0;
		else if (ally_drvdata.led_rgb_data.speed <= 66)
			idx = 1;
		else
			idx = 2;

		rpt.speed = speed_lut[idx];
		rpt.direction = 0x01;
	}

	memcpy(buf, &rpt, sizeof(rpt));

	ret = ally_dev_set_report(led_rgb->hdev, buf, ROG_ALLY_REPORT_SIZE);
	if (ret < 0)
		return ret;

	/* Config (0xb3) stages; SET (0xb5) applies; APPLY (0xb4) commits to NV on suspend. */
	return ally_dev_set_report(led_rgb->hdev, set_buf, sizeof(set_buf));
}

/*
 * Volatile per-zone color set (code page 0xd1, CMD_LED_CONTROL). This is the
 * packet the upstream hid-asus-ally driver streams for all live updates; it
 * bypasses the Aura effect engine and the MCU's non-volatile storage, so it
 * is safe to send at slider rate. Zone order: left-bottom, left-top,
 * right-bottom, right-top.
 */
static int ally_rgb_send_direct(struct ally_rgb_dev *led_rgb,
				u8 red, u8 green, u8 blue)
{
	u8 buf[16] = { FEATURE_KBD_REPORT_ID, HID_ALLY_FEATURE_CODE_PAGE,
		       CMD_LED_CONTROL, 0x0c };
	int i;

	for (i = 0; i < 4; i++) {
		buf[4 + i * 3] = red;
		buf[5 + i * 3] = green;
		buf[6 + i * 3] = blue;
	}

	return ally_dev_set_report(led_rgb->hdev, buf, sizeof(buf));
}

/*
 * Persist the active LED config to MCU non-volatile storage so it is shown
 * at power-on. Each call costs an MCU flash write cycle: call only from the
 * suspend path (mirroring ally_pm_suspend in the upstream hid-asus-ally
 * driver), never from the update hot path.
 */
static int ally_rgb_commit(struct ally_rgb_dev *led_rgb)
{
	u8 apply_buf[ROG_ALLY_REPORT_SIZE] = {
		FEATURE_KBD_REPORT_ID, ALLY_LED_CMD_APPLY
	};

	return ally_dev_set_report(led_rgb->hdev, apply_buf, sizeof(apply_buf));
}

static void ally_rgb_set(struct led_classdev *cdev, enum led_brightness brightness)
{
	struct led_classdev_mc *mc_cdev = lcdev_to_mccdev(cdev);
	struct ally_rgb_dev *led = container_of(mc_cdev, struct ally_rgb_dev, led_rgb_dev);

	/* Scale subled intensity by master brightness for static modes. */
	led_mc_calc_color_components(mc_cdev, brightness);

	scoped_guard(spinlock_irqsave, &led->lock) {
		led->update_rgb = true;
		/* Update effect flag if color changed; brightness changes affect hardware level. */
		if (mc_cdev->subled_info[0].intensity != ally_drvdata.led_rgb_data.red ||
		    mc_cdev->subled_info[1].intensity != ally_drvdata.led_rgb_data.green ||
		    mc_cdev->subled_info[2].intensity != ally_drvdata.led_rgb_data.blue)
			led->update_effect = true;
	}

	ally_drvdata.led_rgb_data.red = mc_cdev->subled_info[0].intensity;
	ally_drvdata.led_rgb_data.green = mc_cdev->subled_info[1].intensity;
	ally_drvdata.led_rgb_data.blue = mc_cdev->subled_info[2].intensity;
	ally_drvdata.led_rgb_data.initialized = true;
	queue_delayed_work(system_wq, &led->work, msecs_to_jiffies(30));
}

static void ally_rgb_work_fn(struct work_struct *work)
{
	struct ally_rgb_dev *led = container_of(work, struct ally_rgb_dev, work.work);
	bool update_rgb, update_effect;
	int ret;

	scoped_guard(spinlock_irqsave, &led->lock) {
		if (led->removed)
			return;
		update_rgb = led->update_rgb;
		update_effect = led->update_effect;
		led->update_rgb = false;
		led->update_effect = false;
	}

	if (!update_rgb && !update_effect)
		return;

	ret = ally_rgb_apply_brightness(led);
	if (ret < 0) {
		dev_err(&led->hdev->dev, "Failed to apply RGB brightness: %d\n", ret);
		return;
	}

	if (ally_drvdata.led_rgb_data.mode == ALLY_RGB_EFFECT_STATIC) {
		/*
		 * Static colors stream on the volatile direct path. The Aura
		 * effect engine (and its NV-backed config) is never touched
		 * here; dimming and "off" are software RGB scaling.
		 */
		if (ally_drvdata.led_rgb_data.enabled)
			ret = ally_rgb_send_direct(led,
				led->led_rgb_dev.subled_info[0].brightness,
				led->led_rgb_dev.subled_info[1].brightness,
				led->led_rgb_dev.subled_info[2].brightness);
		else
			ret = ally_rgb_send_direct(led, 0, 0, 0);
		if (ret < 0)
			dev_err(&led->hdev->dev, "Failed to set direct RGB: %d\n", ret);
	} else if (update_effect) {
		ret = ally_rgb_apply_effect(led);
		if (ret < 0)
			dev_err(&led->hdev->dev, "Failed to apply RGB effect: %d\n", ret);
	}
}

/*
 * Route sysfs-triggered updates through the throttled worker. Sending from
 * the store callbacks directly would bypass rate limiting and interleave
 * with the worker's own packet sequences.
 */
static void ally_rgb_queue_update(bool effect_changed)
{
	struct ally_rgb_dev *led = ally_drvdata.led_rgb_dev;

	if (!led)
		return;

	scoped_guard(spinlock_irqsave, &led->lock) {
		if (led->removed)
			return;
		led->update_rgb = true;
		if (effect_changed)
			led->update_effect = true;
	}
	queue_delayed_work(system_wq, &led->work, msecs_to_jiffies(30));
}



static void ally_rgb_resume_work_fn(struct work_struct *work)
{
	struct ally_rgb_dev *led_rgb = container_of(work, struct ally_rgb_dev, resume_work.work);
	struct mc_subled *mc_led_info;
	struct led_classdev *led_cdev;

	if (!led_rgb || led_rgb->removed)
		return;

	led_cdev = &led_rgb->led_rgb_dev.led_cdev;
	mc_led_info = led_rgb->led_rgb_dev.subled_info;

	if (ally_drvdata.led_rgb_data.initialized) {
		/* MCU rebooted; reset last_hw_level and schedule full update. */
		led_rgb->last_hw_level = -1;
		scoped_guard(spinlock_irqsave, &led_rgb->lock) {
			led_rgb->update_rgb = true;
			led_rgb->update_effect = true;
		}
		queue_delayed_work(system_wq, &led_rgb->work, 0);
	}

	/* Force release all vendor buttons to prevent "stuck" ghosting on resume (workaround for Ally X USB re-probing during suspend/resume)*/
	if (ally_drvdata.keyboard_input) {
		input_report_key(ally_drvdata.keyboard_input, KEY_F16, 0);
		input_report_key(ally_drvdata.keyboard_input, KEY_F17, 0);
		input_report_key(ally_drvdata.keyboard_input, KEY_F18, 0);
		input_report_key(ally_drvdata.keyboard_input, KEY_PROG1, 0);
		input_sync(ally_drvdata.keyboard_input);
	}
	if (ally_drvdata.ally_x_input) {
		input_report_key(ally_drvdata.ally_x_input, KEY_F16, 0);
		input_report_key(ally_drvdata.ally_x_input, KEY_F17, 0);
		input_report_key(ally_drvdata.ally_x_input, KEY_F18, 0);
		input_report_key(ally_drvdata.ally_x_input, KEY_PROG1, 0);
		input_sync(ally_drvdata.ally_x_input);
	}
}

/* Resume LEDs after suspend — called from hid_asus_ally_reset_resume */
static void ally_rgb_resume(void)
{
	struct ally_rgb_dev *led_rgb = ally_drvdata.led_rgb_dev;

	if (!led_rgb)
		return;

	if (ally_drvdata.led_rgb_data.initialized) {
		/* Defer execution so USB reset_resume finishes and MCU is ready */
		schedule_delayed_work(&led_rgb->resume_work, msecs_to_jiffies(1500));
	}
}

/* Ally RGB sysfs attributes 
 * TODO: these are mapped to the Legion Go S's LED names for SteamOS GameMode compatibility, 
 * but will be changed to Asus branded names for upsteamability.
 */

static const char *const ally_rgb_effect_strings[] = {
	[ALLY_RGB_EFFECT_STATIC]	= "monocolor",
	[ALLY_RGB_EFFECT_BREATHING]	= "breathe",
	[ALLY_RGB_EFFECT_COLOR_CYCLE]	= "chroma",
	[ALLY_RGB_EFFECT_RAINBOW]	= "rainbow",
	[ALLY_RGB_EFFECT_STROBE]	= "strobe",
};

static ssize_t effect_show(struct device *dev,
			   struct device_attribute *attr, char *buf)
{
	enum ally_rgb_effect mode = ally_drvdata.led_rgb_data.mode;

	if (mode >= ARRAY_SIZE(ally_rgb_effect_strings))
		return -EINVAL;
	return sysfs_emit(buf, "%s\n", ally_rgb_effect_strings[mode]);
}

static ssize_t effect_store(struct device *dev,
			    struct device_attribute *attr,
			    const char *buf, size_t count)
{
	int mode = sysfs_match_string(ally_rgb_effect_strings, buf);

	if (mode < 0)
		return mode;

	ally_drvdata.led_rgb_data.mode = mode;
	ally_rgb_queue_update(true);

	return count;
}

static ssize_t effect_index_show(struct device *dev,
				 struct device_attribute *attr, char *buf)
{
	int i, len = 0;

	for (i = 0; i < ARRAY_SIZE(ally_rgb_effect_strings); i++)
		len += sysfs_emit_at(buf, len, "%s%s",
				     i ? " " : "", ally_rgb_effect_strings[i]);
	len += sysfs_emit_at(buf, len, "\n");
	return len;
}

static ssize_t speed_show(struct device *dev,
			  struct device_attribute *attr, char *buf)
{
	return sysfs_emit(buf, "%d\n", ally_drvdata.led_rgb_data.speed);
}

static ssize_t speed_store(struct device *dev,
			   struct device_attribute *attr,
			   const char *buf, size_t count)
{
	int ret;
	u8 speed;

	ret = kstrtou8(buf, 10, &speed);
	if (ret)
		return ret;

	if (speed > 100)
		return -EINVAL;

	ally_drvdata.led_rgb_data.speed = speed;
	ally_rgb_queue_update(true);

	return count;
}

static ssize_t speed_range_show(struct device *dev,
				struct device_attribute *attr, char *buf)
{
	return sysfs_emit(buf, "0-100\n");
}

static ssize_t profile_show(struct device *dev,
			    struct device_attribute *attr, char *buf)
{
	return sysfs_emit(buf, "1\n");
}

static ssize_t profile_store(struct device *dev,
			     struct device_attribute *attr,
			     const char *buf, size_t count)
{
	return count;
}

static ssize_t profile_range_show(struct device *dev,
				  struct device_attribute *attr, char *buf)
{
	return sysfs_emit(buf, "1-3\n");
}

static ssize_t enabled_show(struct device *dev,
			    struct device_attribute *attr, char *buf)
{
	return sysfs_emit(buf, "%d\n", ally_drvdata.led_rgb_data.enabled);
}

static ssize_t enabled_store(struct device *dev,
			     struct device_attribute *attr,
			     const char *buf, size_t count)
{
	bool enabled;
	int ret = kstrtobool(buf, &enabled);

	if (ret)
		return ret;

	ally_drvdata.led_rgb_data.enabled = enabled;
	ally_rgb_queue_update(false);

	return count;
}

static ssize_t enabled_index_show(struct device *dev,
				  struct device_attribute *attr, char *buf)
{
	return sysfs_emit(buf, "0 1\n");
}

static DEVICE_ATTR_RW(effect);
static DEVICE_ATTR_RO(effect_index);
static DEVICE_ATTR_RW(speed);
static DEVICE_ATTR_RO(speed_range);
static DEVICE_ATTR_RW(profile);
static DEVICE_ATTR_RO(profile_range);
static DEVICE_ATTR_RW(enabled);
static DEVICE_ATTR_RO(enabled_index);

static struct attribute *ally_rgb_attrs[] = {
	&dev_attr_effect.attr,
	&dev_attr_effect_index.attr,
	&dev_attr_speed.attr,
	&dev_attr_speed_range.attr,
	&dev_attr_profile.attr,
	&dev_attr_profile_range.attr,
	&dev_attr_enabled.attr,
	&dev_attr_enabled_index.attr,
	NULL,
};

static struct attribute_group ally_rgb_attr_group = {
	.attrs = ally_rgb_attrs,
};

static int ally_rgb_register(struct hid_device *hdev, struct ally_rgb_dev *led_rgb)
{
	struct mc_subled *mc_led_info;
	struct led_classdev *led_cdev;
	int ret;

	mc_led_info = devm_kmalloc_array(&hdev->dev, 3, sizeof(*mc_led_info),
					 GFP_KERNEL | __GFP_ZERO);
	if (!mc_led_info)
		return -ENOMEM;

	mc_led_info[0].color_index = LED_COLOR_ID_RED;
	mc_led_info[1].color_index = LED_COLOR_ID_GREEN;
	mc_led_info[2].color_index = LED_COLOR_ID_BLUE;

	led_rgb->led_rgb_dev.subled_info = mc_led_info;
	led_rgb->led_rgb_dev.num_colors = 3;

	led_cdev = &led_rgb->led_rgb_dev.led_cdev;
	led_cdev->brightness = 50;
	led_cdev->name = ALLY_LED_NAME;
	led_cdev->max_brightness = 100;
	led_cdev->brightness_set = ally_rgb_set;
	led_cdev->color = LED_COLOR_ID_RGB;

	/* 
	 * Restore saved intensities after Ally X re-probe. The Multicolor 
	 * LED framework treats this as a fresh device and zeros intensities.
	 */
	if (ally_drvdata.led_rgb_data.initialized) {
		mc_led_info[0].intensity = ally_drvdata.led_rgb_data.red;
		mc_led_info[1].intensity = ally_drvdata.led_rgb_data.green;
		mc_led_info[2].intensity = ally_drvdata.led_rgb_data.blue;
	}

	/* Initialize scaled brightness values */
	led_mc_calc_color_components(&led_rgb->led_rgb_dev, led_cdev->brightness);

	/* Effect mode/speed are restored via ally_drvdata; brightness uses defaults */

	ret = devm_led_classdev_multicolor_register(&hdev->dev, &led_rgb->led_rgb_dev);
	if (ret)
		return ret;

	ret = devm_device_add_group(led_rgb->led_rgb_dev.led_cdev.dev,
				     &ally_rgb_attr_group);
	if (ret && ret != -EEXIST)
		return ret;

	return 0;
}


static struct ally_rgb_dev *ally_rgb_create(struct hid_device *hdev)
{
	struct ally_rgb_dev *led_rgb;
	int ret;

	led_rgb = devm_kzalloc(&hdev->dev, sizeof(struct ally_rgb_dev), GFP_KERNEL);
	if (!led_rgb)
		return ERR_PTR(-ENOMEM);

	led_rgb->hdev = hdev;
	led_rgb->removed = false;

	INIT_DELAYED_WORK(&led_rgb->work, ally_rgb_work_fn);
	INIT_DELAYED_WORK(&led_rgb->resume_work, ally_rgb_resume_work_fn);
	led_rgb->output_worker_initialized = true;
	led_rgb->last_hw_level = -1;
	spin_lock_init(&led_rgb->lock);

	ret = ally_rgb_register(hdev, led_rgb);
	if (ret < 0) {
		cancel_delayed_work_sync(&led_rgb->work);
		cancel_delayed_work_sync(&led_rgb->resume_work);
		devm_kfree(&hdev->dev, led_rgb);
		return ERR_PTR(ret);
	}

	/* Initialize state if not already done */
	if (!ally_drvdata.led_rgb_data.initialized)
		ally_drvdata.led_rgb_data.enabled = true;

	ally_rgb_apply_brightness(led_rgb);

	/* Re-apply saved state after MCU re-init (suspend/resume) */
	if (ally_drvdata.led_rgb_data.initialized) {
		schedule_delayed_work(&led_rgb->resume_work, msecs_to_jiffies(1500));
	}

	return led_rgb;
}

static void ally_rgb_remove(struct hid_device *hdev)
{
	struct ally_rgb_dev *led_rgb = ally_drvdata.led_rgb_dev;
	int ep;

	ep = ally_get_endpoint_address(hdev);
	if (ep != HID_ALLY_INTF_CFG_IN)
		return;

	if (!led_rgb || led_rgb->removed)
		return;

	scoped_guard(spinlock_irqsave, &led_rgb->lock) {
		led_rgb->removed = true;
		led_rgb->output_worker_initialized = false;
	}
	cancel_delayed_work_sync(&led_rgb->work);
	cancel_delayed_work_sync(&led_rgb->resume_work);
	devm_led_classdev_multicolor_unregister(&hdev->dev, &led_rgb->led_rgb_dev);

	hid_info(hdev, "Removed Ally RGB LED interface\n");
}

/******************************************************************************/
/* ROG Ally driver init                                                       */
/******************************************************************************/

/**
 * Initialize ROG Ally HID extension: this module works alongside
 * the main Asus HID driver to handle Ally-specific features
 * and quirks.
 *
 * returns:
 * Either an ally_handheld struct pointer on success, or an ERR_PTR on failure.
 * The caller is not expected to use the returned pointer, but it should
 * check for errors by using IS_ERR and PTR_ERR and pass to other functions
 * NULL if there was an error.
 */
static struct ally_handheld *hid_asus_ally_probe(struct hid_device *hdev)
{
	int ret, ep = ally_get_endpoint_address(hdev);
	struct ally_config *ally_cfg;
	struct hid_input *hidinput;

	if (ep < 0)
		return ERR_PTR(ep);

	scoped_guard(mutex, &ally_data_mutex)
		switch (ep) {
		case HID_ALLY_INTF_CFG_IN:
			ally_drvdata.cfg_hdev = hdev;
			ally_cfg = ally_config_create(hdev, &ally_drvdata);
			if (IS_ERR(ally_cfg)) {
				hid_err(hdev, "Failed to create Ally cfg: %ld\n",
					PTR_ERR(ally_cfg));
				return ERR_PTR(PTR_ERR(ally_cfg));
			}
			ally_drvdata.config = ally_cfg;

			/* Finish the initialization of the MCU */
			ret = hid_asus_ally_init(hdev);
			if (ret < 0)
				return ERR_PTR(ret);


			/* LED ring init — non-fatal if it fails */
			ally_drvdata.led_rgb_dev = ally_rgb_create(hdev);
			if (IS_ERR(ally_drvdata.led_rgb_dev)) {
				hid_warn(hdev, "Failed to create Ally RGB LEDs\n");
				ally_drvdata.led_rgb_dev = NULL;
			} else {
				hid_info(hdev, "Created Ally RGB LED controls\n");
			}
			break;
		case HID_ALLY_X_INTF_IN:
			ally_drvdata.ally_x_hdev = hdev;
			/* This will create and populate ally_x_input */
			ret = ally_x_setup_input(hdev, &ally_drvdata);
			if (ret) {
				hid_err(hdev, "Failed to create Ally X gamepad device.\n");
				return ERR_PTR(ret);
			}
			break;
		case HID_ALLY_INTF_KEYBOARD_IN:
			ally_drvdata.keyboard_hdev = hdev;
			if (!list_empty(&hdev->inputs)) {
				hidinput = list_first_entry(&hdev->inputs, struct hid_input, list);
				ally_drvdata.keyboard_input = hidinput->input;
			}
			break;
		default:
			/* This is normally supposed to happen */
			break;
		}

	return &ally_drvdata;
}

static void hid_asus_ally_remove(struct hid_device *hdev, struct ally_handheld *ally)
{
	if (!ally)
		return;

	scoped_guard(mutex, &ally_data_mutex) {
		if (ally->ally_x_hdev == hdev) {
			if (ally->ff_work_initialized) {
				ally->ff_work_initialized = false;
				cancel_work_sync(&ally->ff_work);
			}
			ally->ally_x_input = NULL;
			ally->ally_x_hdev = NULL;
		}

		if (ally->cfg_hdev == hdev) {
			if (ally->led_rgb_dev) {
				ally_rgb_remove(hdev);
				ally->led_rgb_dev = NULL;
			}
			ally_config_remove(hdev, ally);
			ally->cfg_hdev = NULL;
			ally->config = NULL;
		}
	}
}

static int hid_asus_ally_reset_resume(struct hid_device *hdev, struct ally_handheld *ally)
{
	int ep = ally_get_endpoint_address(hdev);
	int ret;

	if (!ally)
		return -EINVAL;

	if (ep != HID_ALLY_INTF_CFG_IN)
		return 0;

	ret = hid_asus_ally_init(hdev);
	if (ret < 0)
		return ret;

	/* Restore LED state after MCU re-initialization */
	ally_rgb_resume();

	return 0;
}

static void asus_report_contact_down(struct asus_drvdata *drvdat,
		int toolType, u8 *data)
{
	struct input_dev *input = drvdat->input;
	int touch_major, pressure, x, y;

	x = (data[0] & CONTACT_X_MSB_MASK) << 4 | data[1];
	y = drvdat->tp->max_y - ((data[0] & CONTACT_Y_MSB_MASK) << 8 | data[2]);

	input_report_abs(input, ABS_MT_POSITION_X, x);
	input_report_abs(input, ABS_MT_POSITION_Y, y);

	if (drvdat->tp->contact_size < 5)
		return;

	if (toolType == MT_TOOL_PALM) {
		touch_major = MAX_TOUCH_MAJOR;
		pressure = MAX_PRESSURE;
	} else {
		touch_major = (data[3] >> 4) & CONTACT_TOUCH_MAJOR_MASK;
		pressure = data[4] & CONTACT_PRESSURE_MASK;
	}

	input_report_abs(input, ABS_MT_TOUCH_MAJOR, touch_major);
	input_report_abs(input, ABS_MT_PRESSURE, pressure);
}

/* Required for Synaptics Palm Detection */
static void asus_report_tool_width(struct asus_drvdata *drvdat)
{
	struct input_mt *mt = drvdat->input->mt;
	struct input_mt_slot *oldest;
	int oldid, i;

	if (drvdat->tp->contact_size < 5)
		return;

	oldest = NULL;
	oldid = mt->trkid;

	for (i = 0; i < mt->num_slots; ++i) {
		struct input_mt_slot *ps = &mt->slots[i];
		int id = input_mt_get_value(ps, ABS_MT_TRACKING_ID);

		if (id < 0)
			continue;
		if ((id - oldid) & TRKID_SGN) {
			oldest = ps;
			oldid = id;
		}
	}

	if (oldest) {
		input_report_abs(drvdat->input, ABS_TOOL_WIDTH,
			input_mt_get_value(oldest, ABS_MT_TOUCH_MAJOR));
	}
}

static int asus_report_input(struct asus_drvdata *drvdat, u8 *data, int size)
{
	int i, toolType = MT_TOOL_FINGER;
	u8 *contactData = data + 2;

	if (size != drvdat->tp->report_size)
		return 0;

	for (i = 0; i < drvdat->tp->max_contacts; i++) {
		bool down = !!(data[1] & BIT(i+3));

		if (drvdat->tp->contact_size >= 5)
			toolType = contactData[3] & CONTACT_TOOL_TYPE_MASK ?
						MT_TOOL_PALM : MT_TOOL_FINGER;

		input_mt_slot(drvdat->input, i);
		input_mt_report_slot_state(drvdat->input, toolType, down);

		if (down) {
			asus_report_contact_down(drvdat, toolType, contactData);
			contactData += drvdat->tp->contact_size;
		}
	}

	input_report_key(drvdat->input, BTN_LEFT, data[1] & BTN_LEFT_MASK);
	asus_report_tool_width(drvdat);

	input_mt_sync_frame(drvdat->input);
	input_sync(drvdat->input);

	return 1;
}

static int asus_e1239t_event(struct asus_drvdata *drvdat, u8 *data, int size)
{
	if (size != 3)
		return 0;

	/* Handle broken mute key which only sends press events */
	if (!drvdat->tp &&
	    data[0] == 0x02 && data[1] == 0xe2 && data[2] == 0x00) {
		input_report_key(drvdat->input, KEY_MUTE, 1);
		input_sync(drvdat->input);
		input_report_key(drvdat->input, KEY_MUTE, 0);
		input_sync(drvdat->input);
		return 1;
	}

	/* Handle custom touchpad toggle key which only sends press events */
	if (drvdat->tp_kbd_input &&
	    data[0] == 0x05 && data[1] == 0x02 && data[2] == 0x28) {
		input_report_key(drvdat->tp_kbd_input, KEY_F21, 1);
		input_sync(drvdat->tp_kbd_input);
		input_report_key(drvdat->tp_kbd_input, KEY_F21, 0);
		input_sync(drvdat->tp_kbd_input);
		return 1;
	}

	return 0;
}

/*
 * Send events to asus-wmi driver for handling special keys
 */
static int asus_wmi_send_event(struct asus_drvdata *drvdata, u8 code)
{
	int err;
	u32 retval;

	err = asus_wmi_evaluate_method(ASUS_WMI_METHODID_DEVS,
				       ASUS_WMI_METHODID_NOTIF, code, &retval);
	if (err) {
		pr_warn("Failed to notify asus-wmi: %d\n", err);
		return err;
	}

	if (retval != 0) {
		pr_warn("Failed to notify asus-wmi (retval): 0x%x\n", retval);
		return -EIO;
	}

	return 0;
}

static int asus_event(struct hid_device *hdev, struct hid_field *field,
		      struct hid_usage *usage, __s32 value)
{
	struct asus_drvdata *drvdata = hid_get_drvdata(hdev);

	if ((usage->hid & HID_USAGE_PAGE) == HID_UP_ASUSVENDOR &&
	    (usage->hid & HID_USAGE) != 0x00 &&
	    (usage->hid & HID_USAGE) != 0xff && !usage->type) {
		hid_warn(hdev, "Unmapped Asus vendor usagepage code 0x%02x\n",
			 usage->hid & HID_USAGE);
	}

	if (usage->type == EV_KEY && value) {
		switch (usage->code) {
		case KEY_KBDILLUMUP:
			return !asus_hid_event(ASUS_EV_BRTUP);
		case KEY_KBDILLUMDOWN:
			return !asus_hid_event(ASUS_EV_BRTDOWN);
		case KEY_KBDILLUMTOGGLE:
			return !asus_hid_event(ASUS_EV_BRTTOGGLE);
		case KEY_FN_ESC:
			if (drvdata->quirks & QUIRK_HID_FN_LOCK) {
				drvdata->fn_lock = !drvdata->fn_lock;
				schedule_work(&drvdata->fn_lock_sync_work);
			}
			break;
		}
	}

	return 0;
}

static int asus_raw_event(struct hid_device *hdev,
		struct hid_report *report, u8 *data, int size)
{
	struct asus_drvdata *drvdata = hid_get_drvdata(hdev);

	if (drvdata->battery && data[0] == BATTERY_REPORT_ID)
		return asus_report_battery(drvdata, data, size);

	if (drvdata->tp && data[0] == INPUT_REPORT_ID)
		return asus_report_input(drvdata, data, size);

	if (drvdata->quirks & QUIRK_MEDION_E1239T)
		return asus_e1239t_event(drvdata, data, size);

	if (drvdata->quirks & QUIRK_ROG_ALLY_XPAD) {
		/*
		 * The Ally MCU sends a non-standard byte (0xA8) for QAM long-press
		 * release instead of a standard 0x00. We map it to 0x00 here so the
		 * generic parser can natively handle the key release for 0xA7.
		 */
		if (data[0] == 0x5A && data[1] == 0xA8)
			data[1] = 0x00;

		/*
		 * Return -1 to suppress further processing by the generic HID
		 * input parser for reports we fully handle for the Gamepad (0x0B).
		 * If we let 0x0B fall through then the default parser creates a
		 * generic gamepad causing Steam Input overlaps (i.e. L1 stuck on screenshot).
		 */
		if (hid_asus_ally_raw_event(hdev, drvdata->rog_ally, report, data, size))
			return -1;
	}

	/*
	 * Skip these report ID, the device emits a continuous stream associated
	 * with the AURA mode it is in which looks like an 'echo'.
	 */
	if (report->id == FEATURE_KBD_LED_REPORT_ID1 || report->id == FEATURE_KBD_LED_REPORT_ID2)
		return -1;
	if (drvdata->quirks & QUIRK_ROG_NKEY_KEYBOARD) {
		if (report->id == FEATURE_KBD_REPORT_ID) {
			/*
			 * Fn+F5 fan control key - try to send WMI event to toggle fan mode.
			 * If successful, block the event from reaching userspace.
			 * If asus-wmi is unavailable or the call fails, let the event
			 * pass to userspace so it can implement its own fan control.
			 */
			if (data[1] == ASUS_FAN_CTRL_KEY_CODE) {
				int ret = asus_wmi_send_event(drvdata, ASUS_FAN_CTRL_KEY_CODE);

				if (ret == 0) {
					/* Successfully handled by asus-wmi, block event */
					return -1;
				}

				/*
				 * Warn if asus-wmi failed (but not if it's unavailable).
				 * Let the event reach userspace in all failure cases.
				 */
				if (ret != -ENODEV)
					hid_warn(hdev, "Failed to notify asus-wmi: %d\n", ret);
			}

			/*
			 * ASUS ROG laptops send these codes during normal operation
			 * with no discernable reason. Filter them out to avoid
			 * unmapped warning messages.
			 */
			if (data[1] == ASUS_SPURIOUS_CODE_0XEA ||
			    data[1] == ASUS_SPURIOUS_CODE_0XEC ||
			    data[1] == ASUS_SPURIOUS_CODE_0X02 ||
			    data[1] == ASUS_SPURIOUS_CODE_0X8A ||
			    data[1] == ASUS_SPURIOUS_CODE_0X9E) {
				return -1;
			}
		}

		/*
		 * G713 and G733 send these codes on some keypresses, depending on
		 * the key pressed it can trigger a shutdown event if not caught.
		 */
		if (data[0] == 0x02 && data[1] == 0x30)
			return -1;
	}

	if (drvdata->quirks & QUIRK_ROG_CLAYMORE_II_KEYBOARD) {
		/*
		 * CLAYMORE II keyboard sends this packet when it goes to sleep
		 * this causes the whole system to go into suspend.
		 */
		if (size == 2 && data[0] == 0x02 && data[1] == 0x00)
			return -1;
	}

	return 0;
}

static int asus_kbd_set_report(struct hid_device *hdev, const u8 *buf, size_t buf_size)
{
	u8 *dmabuf __free(kfree) = kmemdup(buf, buf_size, GFP_KERNEL);
	if (!dmabuf)
		return -ENOMEM;

	/*
	 * The report ID should be set from the incoming buffer due to LED and key
	 * interfaces having different pages
	 */
	return hid_hw_raw_request(hdev, buf[0], dmabuf, buf_size, HID_FEATURE_REPORT,
				  HID_REQ_SET_REPORT);
}

static int asus_kbd_init(struct hid_device *hdev, u8 report_id)
{
	/*
	 * The handshake is first sent as a set_report, then retrieved
	 * from a get_report. They should be equal.
	 */
	const u8 buf[] = { report_id, 0x41, 0x53, 0x55, 0x53, 0x20, 0x54,
		     0x65, 0x63, 0x68, 0x2e, 0x49, 0x6e, 0x63, 0x2e, 0x00 };
	int ret;

	ret = asus_kbd_set_report(hdev, buf, sizeof(buf));
	if (ret < 0) {
		hid_err(hdev, "Asus handshake %02x failed to send: %d\n",
			report_id, ret);
		return ret;
	}

	u8 *readbuf __free(kfree) = kzalloc(FEATURE_KBD_REPORT_SIZE, GFP_KERNEL);
	if (!readbuf)
		return -ENOMEM;

	ret = hid_hw_raw_request(hdev, report_id, readbuf,
				 FEATURE_KBD_REPORT_SIZE, HID_FEATURE_REPORT,
				 HID_REQ_GET_REPORT);
	if (ret < 0) {
		hid_warn(hdev, "Asus handshake %02x failed to receive ack: %d\n",
			 report_id, ret);
	} else if (memcmp(readbuf, buf, sizeof(buf)) != 0) {
		hid_warn(hdev, "Asus handshake %02x returned invalid response: %*ph\n",
			 report_id, FEATURE_KBD_REPORT_SIZE, readbuf);
	}

	/*
	 * Do not return error if handshake is wrong until this is
	 * verified to work for all devices.
	 */
	return 0;
}

static int asus_kbd_get_functions(struct hid_device *hdev,
				  unsigned char *kbd_func,
				  u8 report_id)
{
	const u8 buf[] = { report_id, 0x05, 0x20, 0x31, 0x00, 0x08 };
	u8 *readbuf;
	int ret;

	ret = asus_kbd_set_report(hdev, buf, sizeof(buf));
	if (ret < 0) {
		hid_err(hdev, "Asus failed to send configuration command: %d\n", ret);
		return ret;
	}

	readbuf = kzalloc(FEATURE_KBD_REPORT_SIZE, GFP_KERNEL);
	if (!readbuf)
		return -ENOMEM;

	ret = hid_hw_raw_request(hdev, report_id, readbuf,
				 FEATURE_KBD_REPORT_SIZE, HID_FEATURE_REPORT,
				 HID_REQ_GET_REPORT);
	if (ret < 0) {
		hid_err(hdev, "Asus failed to request functions: %d\n", ret);
		kfree(readbuf);
		return ret;
	}

	*kbd_func = readbuf[6];

	kfree(readbuf);
	return ret;
}

static int asus_kbd_disable_oobe(struct hid_device *hdev)
{
	const u8 init[][6] = {
		{ FEATURE_KBD_REPORT_ID, 0x05, 0x20, 0x31, 0x00, 0x08 },
		{ FEATURE_KBD_REPORT_ID, 0xBA, 0xC5, 0xC4 },
		{ FEATURE_KBD_REPORT_ID, 0xD0, 0x8F, 0x01 },
		{ FEATURE_KBD_REPORT_ID, 0xD0, 0x85, 0xFF }
	};
	int ret;

	for (size_t i = 0; i < ARRAY_SIZE(init); i++) {
		ret = asus_kbd_set_report(hdev, init[i], sizeof(init[i]));
		if (ret < 0)
			return ret;
	}

	hid_info(hdev, "Disabled OOBE for keyboard\n");
	return 0;
}

static int asus_kbd_set_fn_lock(struct hid_device *hdev, bool enabled)
{
	u8 buf[] = { FEATURE_KBD_REPORT_ID, 0xd0, 0x4e, !!enabled };

	return asus_kbd_set_report(hdev, buf, sizeof(buf));
}

static void asus_sync_fn_lock(struct work_struct *work)
{
	struct asus_drvdata *drvdata =
	container_of(work, struct asus_drvdata, fn_lock_sync_work);

	asus_kbd_set_fn_lock(drvdata->hdev, drvdata->fn_lock);
}

static void asus_schedule_work(struct asus_kbd_leds *led)
{
	unsigned long flags;

	spin_lock_irqsave(&led->lock, flags);
	if (!led->removed)
		schedule_work(&led->work);
	spin_unlock_irqrestore(&led->lock, flags);
}

static void asus_kbd_backlight_set(struct asus_hid_listener *listener,
				   int brightness)
{
	struct asus_kbd_leds *led = container_of(listener, struct asus_kbd_leds,
						 listener);
	unsigned long flags;

	spin_lock_irqsave(&led->lock, flags);
	led->brightness = brightness;
	spin_unlock_irqrestore(&led->lock, flags);

	asus_schedule_work(led);
}

static void asus_kbd_backlight_work(struct work_struct *work)
{
	struct asus_kbd_leds *led = container_of(work, struct asus_kbd_leds, work);
	u8 buf[] = { FEATURE_KBD_REPORT_ID, 0xba, 0xc5, 0xc4, 0x00 };
	int ret;
	unsigned long flags;

	spin_lock_irqsave(&led->lock, flags);
	buf[4] = led->brightness;
	spin_unlock_irqrestore(&led->lock, flags);

	ret = asus_kbd_set_report(led->hdev, buf, sizeof(buf));
	if (ret < 0)
		hid_err(led->hdev, "Asus failed to set keyboard backlight: %d\n", ret);
}

/*
 * We don't care about any other part of the string except the version section.
 * Example strings: FGA80100.RC72LA.312_T01, FGA80100.RC71LS.318_T01
 * The bytes "5a 05 03 31 00 1a 13" and possibly more come before the version
 * string, and there may be additional bytes after the version string such as
 * "75 00 74 00 65 00" or a postfix such as "_T01"
 */
static int mcu_parse_version_string(const u8 *response, size_t response_size)
{
	const u8 *end = response + response_size;
	const u8 *p = response;
	int dots, err, version;
	char buf[4];

	dots = 0;
	while (p < end && dots < 2) {
		if (*p++ == '.')
			dots++;
	}

	if (dots != 2 || p >= end || (p + 3) >= end)
		return -EINVAL;

	memcpy(buf, p, 3);
	buf[3] = '\0';

	err = kstrtoint(buf, 10, &version);
	if (err || version < 0)
		return -EINVAL;

	return version;
}

static int mcu_request_version(struct hid_device *hdev)
{
	u8 *response __free(kfree) = kzalloc(ROG_ALLY_REPORT_SIZE, GFP_KERNEL);
	const u8 request[] = { 0x5a, 0x05, 0x03, 0x31, 0x00, 0x20 };
	int ret;

	if (!response)
		return -ENOMEM;

	ret = asus_kbd_set_report(hdev, request, sizeof(request));
	if (ret < 0)
		return ret;

	ret = hid_hw_raw_request(hdev, FEATURE_REPORT_ID, response,
				ROG_ALLY_REPORT_SIZE, HID_FEATURE_REPORT,
				HID_REQ_GET_REPORT);
	if (ret < 0)
		return ret;

	ret = mcu_parse_version_string(response, ROG_ALLY_REPORT_SIZE);
	if (ret < 0) {
		pr_err("Failed to parse MCU version: %d\n", ret);
		print_hex_dump(KERN_ERR, "MCU: ", DUMP_PREFIX_NONE,
			      16, 1, response, ROG_ALLY_REPORT_SIZE, false);
	}

	return ret;
}

static void validate_mcu_fw_version(struct hid_device *hdev, int idProduct)
{
	int min_version, version;

	version = mcu_request_version(hdev);
	if (version < 0)
		return;

	switch (idProduct) {
	case USB_DEVICE_ID_ASUSTEK_ROG_NKEY_ALLY:
		min_version = ROG_ALLY_MIN_MCU;
		break;
	case USB_DEVICE_ID_ASUSTEK_ROG_NKEY_ALLY_X:
		min_version = ROG_ALLY_X_MIN_MCU;
		break;
	default:
		min_version = 0;
	}

	if (version < min_version) {
		hid_warn(hdev,
			"The MCU firmware version must be %d or greater to avoid issues with suspend.\n",
			min_version);
	} else {
		set_ally_mcu_hack(ASUS_WMI_ALLY_MCU_HACK_DISABLED);
		set_ally_mcu_powersave(true);
	}
}

static bool asus_has_report_id(struct hid_device *hdev, u16 report_id)
{
	struct hid_report *report;
	int t;

	for (t = HID_INPUT_REPORT; t <= HID_FEATURE_REPORT; t++) {
		list_for_each_entry(report, &hdev->report_enum[t].report_list, list) {
			if (report->id == report_id)
				return true;
		}
	}

	return false;
}

static int asus_kbd_register_leds(struct hid_device *hdev)
{
	struct asus_drvdata *drvdata = hid_get_drvdata(hdev);
	struct usb_interface *intf;
	struct usb_device *udev;
	unsigned char kbd_func;
	int ret;

	/* Get keyboard functions */
	ret = asus_kbd_get_functions(hdev, &kbd_func, FEATURE_KBD_REPORT_ID);
	if (ret < 0)
		return ret;

	/* Check for backlight support */
	if (!(kbd_func & SUPPORT_KBD_BACKLIGHT))
		return -ENODEV;

	if (dmi_match(DMI_PRODUCT_FAMILY, "ProArt P16")) {
		ret = asus_kbd_disable_oobe(hdev);
		if (ret < 0)
			return ret;
	}

	if (drvdata->quirks & QUIRK_ROG_ALLY_XPAD) {
		intf = to_usb_interface(hdev->dev.parent);
		udev = interface_to_usbdev(intf);
		validate_mcu_fw_version(hdev,
			le16_to_cpu(udev->descriptor.idProduct));
	}

	drvdata->kbd_backlight = devm_kzalloc(&hdev->dev,
					      sizeof(struct asus_kbd_leds),
					      GFP_KERNEL);
	if (!drvdata->kbd_backlight)
		return -ENOMEM;

	drvdata->kbd_backlight->removed = false;
	drvdata->kbd_backlight->brightness = 0;
	drvdata->kbd_backlight->hdev = hdev;
	drvdata->kbd_backlight->listener.brightness_set = asus_kbd_backlight_set;
	INIT_WORK(&drvdata->kbd_backlight->work, asus_kbd_backlight_work);
	spin_lock_init(&drvdata->kbd_backlight->lock);

	ret = asus_hid_register_listener(&drvdata->kbd_backlight->listener);
	if (ret < 0) {
		/* No need to have this still around */
		devm_kfree(&hdev->dev, drvdata->kbd_backlight);
	}

	return ret;
}

/*
 * [0]       REPORT_ID (same value defined in report descriptor)
 * [1]	     rest battery level. range [0..255]
 * [2]..[7]  Bluetooth hardware address (MAC address)
 * [8]       charging status
 *            = 0 : AC offline / discharging
 *            = 1 : AC online  / charging
 *            = 2 : AC online  / fully charged
 */
static int asus_parse_battery(struct asus_drvdata *drvdata, u8 *data, int size)
{
	u8 sts;
	u8 lvl;
	int val;

	lvl = data[1];
	sts = data[8];

	drvdata->battery_capacity = ((int)lvl * 100) / (int)BATTERY_LEVEL_MAX;

	switch (sts) {
	case BATTERY_STAT_CHARGING:
		val = POWER_SUPPLY_STATUS_CHARGING;
		break;
	case BATTERY_STAT_FULL:
		val = POWER_SUPPLY_STATUS_FULL;
		break;
	case BATTERY_STAT_DISCONNECT:
	default:
		val = POWER_SUPPLY_STATUS_DISCHARGING;
		break;
	}
	drvdata->battery_stat = val;

	return 0;
}

static int asus_report_battery(struct asus_drvdata *drvdata, u8 *data, int size)
{
	/* notify only the autonomous event by device */
	if ((drvdata->battery_in_query == false) &&
			 (size == BATTERY_REPORT_SIZE))
		power_supply_changed(drvdata->battery);

	return 0;
}

static int asus_battery_query(struct asus_drvdata *drvdata)
{
	u8 *buf;
	int ret = 0;

	buf = kmalloc(BATTERY_REPORT_SIZE, GFP_KERNEL);
	if (!buf)
		return -ENOMEM;

	drvdata->battery_in_query = true;
	ret = hid_hw_raw_request(drvdata->hdev, BATTERY_REPORT_ID,
				buf, BATTERY_REPORT_SIZE,
				HID_INPUT_REPORT, HID_REQ_GET_REPORT);
	drvdata->battery_in_query = false;
	if (ret == BATTERY_REPORT_SIZE)
		ret = asus_parse_battery(drvdata, buf, BATTERY_REPORT_SIZE);
	else
		ret = -ENODATA;

	kfree(buf);

	return ret;
}

static enum power_supply_property asus_battery_props[] = {
	POWER_SUPPLY_PROP_STATUS,
	POWER_SUPPLY_PROP_PRESENT,
	POWER_SUPPLY_PROP_CAPACITY,
	POWER_SUPPLY_PROP_SCOPE,
	POWER_SUPPLY_PROP_MODEL_NAME,
};

#define	QUERY_MIN_INTERVAL	(60 * HZ)	/* 60[sec] */

static int asus_battery_get_property(struct power_supply *psy,
				enum power_supply_property psp,
				union power_supply_propval *val)
{
	struct asus_drvdata *drvdata = power_supply_get_drvdata(psy);
	int ret = 0;

	switch (psp) {
	case POWER_SUPPLY_PROP_STATUS:
	case POWER_SUPPLY_PROP_CAPACITY:
		if (time_before(drvdata->battery_next_query, jiffies)) {
			drvdata->battery_next_query =
					 jiffies + QUERY_MIN_INTERVAL;
			ret = asus_battery_query(drvdata);
			if (ret)
				return ret;
		}
		if (psp == POWER_SUPPLY_PROP_STATUS)
			val->intval = drvdata->battery_stat;
		else
			val->intval = drvdata->battery_capacity;
		break;
	case POWER_SUPPLY_PROP_PRESENT:
		val->intval = 1;
		break;
	case POWER_SUPPLY_PROP_SCOPE:
		val->intval = POWER_SUPPLY_SCOPE_DEVICE;
		break;
	case POWER_SUPPLY_PROP_MODEL_NAME:
		val->strval = drvdata->hdev->name;
		break;
	default:
		ret = -EINVAL;
		break;
	}

	return ret;
}

static int asus_battery_probe(struct hid_device *hdev)
{
	struct asus_drvdata *drvdata = hid_get_drvdata(hdev);
	struct power_supply_config pscfg = { .drv_data = drvdata };
	int ret = 0;

	drvdata->battery_capacity = 0;
	drvdata->battery_stat = POWER_SUPPLY_STATUS_UNKNOWN;
	drvdata->battery_in_query = false;

	drvdata->battery_desc.properties = asus_battery_props;
	drvdata->battery_desc.num_properties = ARRAY_SIZE(asus_battery_props);
	drvdata->battery_desc.get_property = asus_battery_get_property;
	drvdata->battery_desc.type = POWER_SUPPLY_TYPE_BATTERY;
	drvdata->battery_desc.use_for_apm = 0;
	drvdata->battery_desc.name = devm_kasprintf(&hdev->dev, GFP_KERNEL,
					"asus-keyboard-%s-battery",
					strlen(hdev->uniq) ?
					hdev->uniq : dev_name(&hdev->dev));
	if (!drvdata->battery_desc.name)
		return -ENOMEM;

	drvdata->battery_next_query = jiffies;

	drvdata->battery = devm_power_supply_register(&hdev->dev,
				&(drvdata->battery_desc), &pscfg);
	if (IS_ERR(drvdata->battery)) {
		ret = PTR_ERR(drvdata->battery);
		drvdata->battery = NULL;
		hid_err(hdev, "Unable to register battery device\n");
		return ret;
	}

	power_supply_powers(drvdata->battery, &hdev->dev);

	return ret;
}

static int asus_input_configured(struct hid_device *hdev, struct hid_input *hi)
{
	struct input_dev *input = hi->input;
	struct asus_drvdata *drvdata = hid_get_drvdata(hdev);

	/* T100CHI uses MULTI_INPUT, bind the touchpad to the mouse hid_input */
	if (drvdata->quirks & QUIRK_T100CHI &&
	    hi->report->id != T100CHI_MOUSE_REPORT_ID)
		return 0;

	/* Handle MULTI_INPUT on E1239T mouse/touchpad USB interface */
	if (drvdata->tp && (drvdata->quirks & QUIRK_MEDION_E1239T)) {
		switch (hi->report->id) {
		case E1239T_TP_TOGGLE_REPORT_ID:
			input_set_capability(input, EV_KEY, KEY_F21);
			input->name = "Asus Touchpad Keys";
			drvdata->tp_kbd_input = input;
			return 0;
		case INPUT_REPORT_ID:
			break; /* Touchpad report, handled below */
		default:
			return 0; /* Ignore other reports */
		}
	}

	if (drvdata->tp) {
		int ret;

		input_set_abs_params(input, ABS_MT_POSITION_X, 0,
				     drvdata->tp->max_x, 0, 0);
		input_set_abs_params(input, ABS_MT_POSITION_Y, 0,
				     drvdata->tp->max_y, 0, 0);
		input_abs_set_res(input, ABS_MT_POSITION_X, drvdata->tp->res_x);
		input_abs_set_res(input, ABS_MT_POSITION_Y, drvdata->tp->res_y);

		if (drvdata->tp->contact_size >= 5) {
			input_set_abs_params(input, ABS_TOOL_WIDTH, 0,
					     MAX_TOUCH_MAJOR, 0, 0);
			input_set_abs_params(input, ABS_MT_TOUCH_MAJOR, 0,
					     MAX_TOUCH_MAJOR, 0, 0);
			input_set_abs_params(input, ABS_MT_PRESSURE, 0,
					      MAX_PRESSURE, 0, 0);
		}

		__set_bit(BTN_LEFT, input->keybit);
		__set_bit(INPUT_PROP_BUTTONPAD, input->propbit);

		ret = input_mt_init_slots(input, drvdata->tp->max_contacts,
					  INPUT_MT_POINTER);

		if (ret) {
			hid_err(hdev, "Asus input mt init slots failed: %d\n", ret);
			return ret;
		}
	}

	drvdata->input = input;

	if (drvdata->quirks & QUIRK_HID_FN_LOCK) {
		drvdata->fn_lock = true;
		INIT_WORK(&drvdata->fn_lock_sync_work, asus_sync_fn_lock);
		asus_kbd_set_fn_lock(hdev, true);
	}

	return 0;
}

#define asus_map_key_clear(c)	hid_map_usage_clear(hi, usage, bit, \
						    max, EV_KEY, (c))
static int asus_input_mapping(struct hid_device *hdev,
		struct hid_input *hi, struct hid_field *field,
		struct hid_usage *usage, unsigned long **bit,
		int *max)
{
	struct asus_drvdata *drvdata = hid_get_drvdata(hdev);

	if (drvdata->quirks & QUIRK_SKIP_INPUT_MAPPING) {
		/* Don't map anything from the HID report.
		 * We do it all manually in asus_input_configured
		 */
		return -1;
	}

	/*
	 * Ignore a bunch of bogus collections in the T100CHI descriptor.
	 * This avoids a bunch of non-functional hid_input devices getting
	 * created because of the T100CHI using HID_QUIRK_MULTI_INPUT.
	 */
	if ((drvdata->quirks & (QUIRK_T100CHI | QUIRK_T90CHI)) &&
	    (field->application == (HID_UP_GENDESK | 0x0080) ||
	     field->application == HID_GD_MOUSE ||
	     usage->hid == (HID_UP_GENDEVCTRLS | 0x0024) ||
	     usage->hid == (HID_UP_GENDEVCTRLS | 0x0025) ||
	     usage->hid == (HID_UP_GENDEVCTRLS | 0x0026)))
		return -1;

	/* ASUS-specific keyboard hotkeys and led backlight */
	if ((usage->hid & HID_USAGE_PAGE) == HID_UP_ASUSVENDOR) {
		switch (usage->hid & HID_USAGE) {
		case 0x10: asus_map_key_clear(KEY_BRIGHTNESSDOWN);	break;
		case 0x20: asus_map_key_clear(KEY_BRIGHTNESSUP);		break;
		case 0x35: asus_map_key_clear(KEY_DISPLAY_OFF);		break;
		case 0x6c: asus_map_key_clear(KEY_SLEEP);		break;
		case 0x7c: asus_map_key_clear(KEY_MICMUTE);		break;
		case 0x82: asus_map_key_clear(KEY_CAMERA);		break;
		case 0x88: asus_map_key_clear(KEY_RFKILL);			break;
		case 0xb5: asus_map_key_clear(KEY_CALC);			break;
		case 0xc4: asus_map_key_clear(KEY_KBDILLUMUP);		break;
		case 0xc5: asus_map_key_clear(KEY_KBDILLUMDOWN);		break;
		case 0xc7: asus_map_key_clear(KEY_KBDILLUMTOGGLE);	break;
		case 0x4e: asus_map_key_clear(KEY_FN_ESC);		break;
		case 0x7e: asus_map_key_clear(KEY_EMOJI_PICKER);	break;

		case 0x8b: asus_map_key_clear(KEY_PROG1);	break; /* ProArt Creator Hub key */
		case 0x6b: asus_map_key_clear(KEY_F21);		break; /* ASUS touchpad toggle */
		case 0x38: asus_map_key_clear(KEY_PROG1);	break; /* ROG key */
		case 0x93: asus_map_key_clear(KEY_PROG1);	break; /* ROG Ally X right AC button */
		case 0xba: asus_map_key_clear(KEY_PROG2);	break; /* Fn+C ASUS Splendid */
		case 0x5c: asus_map_key_clear(KEY_PROG3);	break; /* Fn+Space Power4Gear */
		case 0x99: asus_map_key_clear(KEY_PROG4);	break; /* Fn+F5 "fan" symbol */
		case 0xae: asus_map_key_clear(KEY_PROG4);	break; /* Fn+F5 "fan" symbol */
		case 0x92: asus_map_key_clear(KEY_CALC);	break; /* Fn+Ret "Calc" symbol */
		case 0xb2: asus_map_key_clear(KEY_PROG2);	break; /* Fn+Left previous aura */
		case 0xb3: asus_map_key_clear(KEY_PROG3);	break; /* Fn+Left next aura */
		case 0x6a: asus_map_key_clear(KEY_F13);		break; /* Screenpad toggle */
		case 0x4b: asus_map_key_clear(KEY_F14);		break; /* Arrows/Pg-Up/Dn toggle */
		case 0xa5: asus_map_key_clear(KEY_F15);		break; /* ROG Ally left back */
		case 0xa6: asus_map_key_clear(KEY_F16);		break; /* ROG Ally QAM button */
		case 0xa7: asus_map_key_clear(KEY_F17);		break; /* ROG Ally ROG long-press */

		default:
			/* ASUS lazily declares 256 usages, ignore the rest,
			 * as some make the keyboard appear as a pointer device. */
			return -1;
		}

		set_bit(EV_REP, hi->input->evbit);
		return 1;
	}

	if ((usage->hid & HID_USAGE_PAGE) == HID_UP_MSVENDOR) {
		switch (usage->hid & HID_USAGE) {
		case 0xff01: asus_map_key_clear(BTN_1);	break;
		case 0xff02: asus_map_key_clear(BTN_2);	break;
		case 0xff03: asus_map_key_clear(BTN_3);	break;
		case 0xff04: asus_map_key_clear(BTN_4);	break;
		case 0xff05: asus_map_key_clear(BTN_5);	break;
		case 0xff06: asus_map_key_clear(BTN_6);	break;
		case 0xff07: asus_map_key_clear(BTN_7);	break;
		case 0xff08: asus_map_key_clear(BTN_8);	break;
		case 0xff09: asus_map_key_clear(BTN_9);	break;
		case 0xff0a: asus_map_key_clear(BTN_A);	break;
		case 0xff0b: asus_map_key_clear(BTN_B);	break;
		case 0x00f1: asus_map_key_clear(KEY_WLAN);	break;
		case 0x00f2: asus_map_key_clear(KEY_BRIGHTNESSDOWN);	break;
		case 0x00f3: asus_map_key_clear(KEY_BRIGHTNESSUP);	break;
		case 0x00f4: asus_map_key_clear(KEY_DISPLAY_OFF);	break;
		case 0x00f7: asus_map_key_clear(KEY_CAMERA);	break;
		case 0x00f8: asus_map_key_clear(KEY_PROG1);	break;
		default:
			return 0;
		}

		set_bit(EV_REP, hi->input->evbit);
		return 1;
	}

	if (drvdata->quirks & QUIRK_NO_CONSUMER_USAGES &&
		(usage->hid & HID_USAGE_PAGE) == HID_UP_CONSUMER) {
		switch (usage->hid & HID_USAGE) {
		case 0xe2: /* Mute */
		case 0xe9: /* Volume up */
		case 0xea: /* Volume down */
			return 0;
		default:
			/* Ignore dummy Consumer usages which make the
			 * keyboard incorrectly appear as a pointer device.
			 */
			return -1;
		}
	}

	/*
	 * The mute button is broken and only sends press events, we
	 * deal with this in our raw_event handler, so do not map it.
	 */
	if ((drvdata->quirks & QUIRK_MEDION_E1239T) &&
	    usage->hid == (HID_UP_CONSUMER | 0xe2)) {
		input_set_capability(hi->input, EV_KEY, KEY_MUTE);
		return -1;
	}

	return 0;
}

static int asus_start_multitouch(struct hid_device *hdev)
{
	int ret;
	static const unsigned char buf[] = {
		FEATURE_REPORT_ID, 0x00, 0x03, 0x01, 0x00
	};
	unsigned char *dmabuf = kmemdup(buf, sizeof(buf), GFP_KERNEL);

	if (!dmabuf) {
		ret = -ENOMEM;
		hid_err(hdev, "Asus failed to alloc dma buf: %d\n", ret);
		return ret;
	}

	ret = hid_hw_raw_request(hdev, dmabuf[0], dmabuf, sizeof(buf),
					HID_FEATURE_REPORT, HID_REQ_SET_REPORT);

	kfree(dmabuf);

	if (ret != sizeof(buf)) {
		hid_err(hdev, "Asus failed to start multitouch: %d\n", ret);
		return ret;
	}

	return 0;
}

static int asus_initialize_reports(struct hid_device *hdev)
{
	int ret;

	for (int r = 0; r < ARRAY_SIZE(asus_report_id_init); r++) {
		if (asus_has_report_id(hdev, asus_report_id_init[r])) {
			ret = asus_kbd_init(hdev, asus_report_id_init[r]);
			if (ret < 0)
				hid_warn(hdev, "Failed to initialize 0x%x: %d.\n",
					 asus_report_id_init[r], ret);
		}
	}

	return 0;
}

static int __maybe_unused asus_resume(struct hid_device *hdev)
{
	struct asus_drvdata *drvdata = hid_get_drvdata(hdev);
	int ret = 0;

	if (drvdata->kbd_backlight) {
		const u8 buf[] = { FEATURE_KBD_REPORT_ID, 0xba, 0xc5, 0xc4,
				drvdata->kbd_backlight->brightness };
		ret = asus_kbd_set_report(hdev, buf, sizeof(buf));
		if (ret < 0) {
			hid_err(hdev, "Asus failed to set keyboard backlight: %d\n", ret);
			goto asus_resume_err;
		}
	}

asus_resume_err:
	return ret;
}

static int __maybe_unused asus_suspend(struct hid_device *hdev, pm_message_t message)
{
	struct asus_drvdata *drvdata = hid_get_drvdata(hdev);

	if (drvdata->quirks & QUIRK_ROG_ALLY_XPAD) {
		struct ally_rgb_dev *led_rgb = ally_drvdata.led_rgb_dev;

		/*
		 * Persist the current LED config so the MCU shows it at
		 * power-on. The 0xb4 NV commit is sent here and only here:
		 * one flash write per suspend instead of one per slider tick.
		 */
		if (led_rgb && led_rgb->hdev == hdev &&
		    ally_drvdata.led_rgb_data.initialized) {
			cancel_delayed_work_sync(&led_rgb->work);
			if (ally_rgb_apply_effect(led_rgb) >= 0)
				ally_rgb_commit(led_rgb);
		}
	}

	return 0;
}

static int __maybe_unused asus_reset_resume(struct hid_device *hdev)
{
	struct asus_drvdata *drvdata = hid_get_drvdata(hdev);
	int ret;

	ret = asus_initialize_reports(hdev);
	if (ret) {
		hid_err(hdev, "Asus initialize reports failed: %d\n", ret);
		goto asus_reset_resume_err;
	}

	if (drvdata->tp)
		return asus_start_multitouch(hdev);

	if (drvdata->quirks & QUIRK_ROG_ALLY_XPAD) {
		ret = hid_asus_ally_reset_resume(hdev, drvdata->rog_ally);
		if (ret) {
			hid_err(hdev, "Failed to resume ROG Ally HID extensions: %d\n", ret);
			goto asus_reset_resume_err;
		}
	}

	return 0;
asus_reset_resume_err:
	return ret;
}
static int asus_probe(struct hid_device *hdev, const struct hid_device_id *id)
{
	struct hid_report_enum *rep_enum;
	struct asus_drvdata *drvdata;
	struct ally_handheld *ally;
	struct hid_report *rep;
	bool is_vendor = false;
	int ret;

	drvdata = devm_kzalloc(&hdev->dev, sizeof(*drvdata), GFP_KERNEL);
	if (drvdata == NULL) {
		hid_err(hdev, "Can't alloc Asus descriptor\n");
		return -ENOMEM;
	}

	hid_set_drvdata(hdev, drvdata);

	drvdata->quirks = id->driver_data;

	/*
	 * T90CHI's keyboard dock returns same ID values as T100CHI's dock.
	 * Thus, identify T90CHI dock with product name string.
	 */
	if (strstr(hdev->name, "T90CHI")) {
		drvdata->quirks &= ~QUIRK_T100CHI;
		drvdata->quirks |= QUIRK_T90CHI;
	}

	if (drvdata->quirks & QUIRK_IS_MULTITOUCH)
		drvdata->tp = &asus_i2c_tp;

	if ((drvdata->quirks & QUIRK_T100_KEYBOARD) && hid_is_usb(hdev)) {
		struct usb_interface *intf = to_usb_interface(hdev->dev.parent);

		if (intf->altsetting->desc.bInterfaceNumber == T100_TPAD_INTF) {
			drvdata->quirks = QUIRK_SKIP_INPUT_MAPPING;
			/*
			 * The T100HA uses the same USB-ids as the T100TAF and
			 * the T200TA uses the same USB-ids as the T100TA, while
			 * both have different max x/y values as the T100TA[F].
			 */
			if (dmi_match(DMI_PRODUCT_NAME, "T100HAN"))
				drvdata->tp = &asus_t100ha_tp;
			else if (dmi_match(DMI_PRODUCT_NAME, "T200TA"))
				drvdata->tp = &asus_t200ta_tp;
			else
				drvdata->tp = &asus_t100ta_tp;
		}
	}

	if (drvdata->quirks & QUIRK_T100CHI) {
		/*
		 * All functionality is on a single HID interface and for
		 * userspace the touchpad must be a separate input_dev.
		 */
		hdev->quirks |= HID_QUIRK_MULTI_INPUT;
		drvdata->tp = &asus_t100chi_tp;
	}

	if ((drvdata->quirks & QUIRK_MEDION_E1239T) && hid_is_usb(hdev)) {
		struct usb_host_interface *alt =
			to_usb_interface(hdev->dev.parent)->altsetting;

		if (alt->desc.bInterfaceNumber == MEDION_E1239T_TPAD_INTF) {
			/* For separate input-devs for tp and tp toggle key */
			hdev->quirks |= HID_QUIRK_MULTI_INPUT;
			drvdata->quirks |= QUIRK_SKIP_INPUT_MAPPING;
			drvdata->tp = &medion_e1239t_tp;
		}
	}

	if (drvdata->quirks & QUIRK_NO_INIT_REPORTS)
		hdev->quirks |= HID_QUIRK_NO_INIT_REPORTS;

	drvdata->hdev = hdev;

	if (drvdata->quirks & (QUIRK_T100CHI | QUIRK_T90CHI)) {
		ret = asus_battery_probe(hdev);
		if (ret) {
			hid_err(hdev,
			    "Asus hid battery_probe failed: %d\n", ret);
			return ret;
		}
	}
	ret = hid_parse(hdev);
	if (ret) {
		hid_err(hdev, "Asus hid parse failed: %d\n", ret);
		return ret;
	}

	/* Check for vendor for RGB init and handle generic devices properly. */
	rep_enum = &hdev->report_enum[HID_INPUT_REPORT];
	list_for_each_entry(rep, &rep_enum->report_list, list) {
		if ((rep->application & HID_USAGE_PAGE) == HID_UP_ASUSVENDOR)
			is_vendor = true;
	}

	ret = hid_hw_start(hdev, HID_CONNECT_DEFAULT);
	if (ret) {
		hid_err(hdev, "Asus hw start failed: %d\n", ret);
		return ret;
	}

	ret = asus_initialize_reports(hdev);
	if (ret) {
		hid_err(hdev, "Asus initialize reports failed: %d\n", ret);
		goto err_stop_hw;
	}

	/* Laptops keyboard backlight is always at 0x5a */
	if (is_vendor && (drvdata->quirks & QUIRK_USE_KBD_BACKLIGHT) &&
	    (asus_has_report_id(hdev, FEATURE_KBD_REPORT_ID)) &&
		(asus_kbd_register_leds(hdev)))
		hid_warn(hdev, "Failed to initialize backlight.\n");

	if (drvdata->quirks & QUIRK_ROG_ALLY_XPAD) {
		ally = hid_asus_ally_probe(hdev);
		if (IS_ERR(ally))
			hid_err(hdev, "Failed to initialize ROG Ally HID extensions: %ld\n",
				PTR_ERR(ally));
		else
			drvdata->rog_ally = ally;
	}

	/*
	 * For ROG keyboards, skip rename for consistency and ->input check as
	 * some devices do not have inputs.
	 */
	if (drvdata->quirks & QUIRK_ROG_NKEY_KEYBOARD)
		return 0;

	/*
	 * Check that input registration succeeded. Checking that
	 * HID_CLAIMED_INPUT is set prevents a UAF when all input devices
	 * were freed during registration due to no usages being mapped,
	 * leaving drvdata->input pointing to freed memory.
	 */
	if (drvdata->input && (hdev->claimed & HID_CLAIMED_INPUT)) {
		if (drvdata->tp)
			drvdata->input->name = "Asus TouchPad";
		else
			drvdata->input->name = "Asus Keyboard";

		if (drvdata->tp) {
			ret = asus_start_multitouch(hdev);
			if (ret)
				goto err_stop_hw;
		}
	}

	return 0;
err_stop_hw:
	hid_hw_stop(hdev);
	return ret;
}

static void asus_remove(struct hid_device *hdev)
{
	struct asus_drvdata *drvdata = hid_get_drvdata(hdev);
	unsigned long flags;

	if (drvdata->quirks & QUIRK_ROG_ALLY_XPAD)
		hid_asus_ally_remove(hdev, drvdata->rog_ally);

	if (drvdata->kbd_backlight) {
		asus_hid_unregister_listener(&drvdata->kbd_backlight->listener);

		spin_lock_irqsave(&drvdata->kbd_backlight->lock, flags);
		drvdata->kbd_backlight->removed = true;
		spin_unlock_irqrestore(&drvdata->kbd_backlight->lock, flags);

		cancel_work_sync(&drvdata->kbd_backlight->work);
	}

	if (drvdata->quirks & QUIRK_HID_FN_LOCK)
		cancel_work_sync(&drvdata->fn_lock_sync_work);

	hid_hw_stop(hdev);
}

static const __u8 asus_g752_fixed_rdesc[] = {
        0x19, 0x00,			/*   Usage Minimum (0x00)       */
        0x2A, 0xFF, 0x00,		/*   Usage Maximum (0xFF)       */
};

static const __u8 *asus_report_fixup(struct hid_device *hdev, __u8 *rdesc,
		unsigned int *rsize)
{
	struct asus_drvdata *drvdata = hid_get_drvdata(hdev);

	if (drvdata->quirks & QUIRK_FIX_NOTEBOOK_REPORT &&
			*rsize >= 56 && rdesc[54] == 0x25 && rdesc[55] == 0x65) {
		hid_info(hdev, "Fixing up Asus notebook report descriptor\n");
		rdesc[55] = 0xdd;
	}
	/* For the T100TA/T200TA keyboard dock */
	if (drvdata->quirks & QUIRK_T100_KEYBOARD &&
		 (*rsize == 76 || *rsize == 101) &&
		 rdesc[73] == 0x81 && rdesc[74] == 0x01) {
		hid_info(hdev, "Fixing up Asus T100 keyb report descriptor\n");
		rdesc[74] &= ~HID_MAIN_ITEM_CONSTANT;
	}
	/* For the T100CHI/T90CHI keyboard dock */
	if (drvdata->quirks & (QUIRK_T100CHI | QUIRK_T90CHI)) {
		int rsize_orig;
		int offs;

		if (drvdata->quirks & QUIRK_T100CHI) {
			rsize_orig = 403;
			offs = 388;
		} else {
			rsize_orig = 306;
			offs = 291;
		}

		/*
		 * Change Usage (76h) to Usage Minimum (00h), Usage Maximum
		 * (FFh) and clear the flags in the Input() byte.
		 * Note the descriptor has a bogus 0 byte at the end so we
		 * only need 1 extra byte.
		 */
		if (*rsize == rsize_orig &&
			rdesc[offs] == 0x09 && rdesc[offs + 1] == 0x76) {
			__u8 *new_rdesc;

			new_rdesc = devm_kzalloc(&hdev->dev, rsize_orig + 1,
						 GFP_KERNEL);
			if (!new_rdesc)
				return rdesc;

			hid_info(hdev, "Fixing up %s keyb report descriptor\n",
				drvdata->quirks & QUIRK_T100CHI ?
				"T100CHI" : "T90CHI");

			memcpy(new_rdesc, rdesc, rsize_orig);
			*rsize = rsize_orig + 1;
			rdesc = new_rdesc;

			memmove(rdesc + offs + 4, rdesc + offs + 2, 12);
			rdesc[offs] = 0x19;
			rdesc[offs + 1] = 0x00;
			rdesc[offs + 2] = 0x29;
			rdesc[offs + 3] = 0xff;
			rdesc[offs + 14] = 0x00;
		}
	}

	if (drvdata->quirks & QUIRK_G752_KEYBOARD &&
		 *rsize == 75 && rdesc[61] == 0x15 && rdesc[62] == 0x00) {
		/* report is missing usage minimum and maximum */
		__u8 *new_rdesc;
		size_t new_size = *rsize + sizeof(asus_g752_fixed_rdesc);

		new_rdesc = devm_kzalloc(&hdev->dev, new_size, GFP_KERNEL);
		if (new_rdesc == NULL)
			return rdesc;

		hid_info(hdev, "Fixing up Asus G752 keyb report descriptor\n");
		/* copy the valid part */
		memcpy(new_rdesc, rdesc, 61);
		/* insert missing part */
		memcpy(new_rdesc + 61, asus_g752_fixed_rdesc, sizeof(asus_g752_fixed_rdesc));
		/* copy remaining data */
		memcpy(new_rdesc + 61 + sizeof(asus_g752_fixed_rdesc), rdesc + 61, *rsize - 61);

		*rsize = new_size;
		rdesc = new_rdesc;
	}

	if (drvdata->quirks & QUIRK_ROG_NKEY_KEYBOARD &&
			*rsize == 331 && rdesc[190] == 0x85 && rdesc[191] == 0x5a &&
			rdesc[204] == 0x95 && rdesc[205] == 0x05) {
		hid_info(hdev, "Fixing up Asus N-KEY keyb report descriptor\n");
		rdesc[205] = 0x01;
	}

	/* match many more n-key devices */
	if (drvdata->quirks & QUIRK_ROG_NKEY_KEYBOARD && *rsize > 15) {
		for (int i = 0; i < *rsize - 15; i++) {
			/* offset to the count from 0x5a report part always 14 */
			if (rdesc[i] == 0x85 && rdesc[i + 1] == 0x5a &&
			    rdesc[i + 14] == 0x95 && rdesc[i + 15] == 0x05) {
				hid_info(hdev, "Fixing up Asus N-Key report descriptor\n");
				rdesc[i + 15] = 0x01;
				break;
			}
		}
	}

	return rdesc;
}

static const struct hid_device_id asus_devices[] = {
	{ HID_I2C_DEVICE(USB_VENDOR_ID_ASUSTEK,
		USB_DEVICE_ID_ASUSTEK_I2C_KEYBOARD), I2C_KEYBOARD_QUIRKS},
	{ HID_I2C_DEVICE(USB_VENDOR_ID_ASUSTEK,
		USB_DEVICE_ID_ASUSTEK_I2C_TOUCHPAD), I2C_TOUCHPAD_QUIRKS },
	{ HID_USB_DEVICE(USB_VENDOR_ID_ASUSTEK,
		USB_DEVICE_ID_ASUSTEK_ROG_KEYBOARD1), QUIRK_USE_KBD_BACKLIGHT },
	{ HID_USB_DEVICE(USB_VENDOR_ID_ASUSTEK,
		USB_DEVICE_ID_ASUSTEK_ROG_KEYBOARD2), QUIRK_USE_KBD_BACKLIGHT },
	{ HID_USB_DEVICE(USB_VENDOR_ID_ASUSTEK,
		USB_DEVICE_ID_ASUSTEK_ROG_KEYBOARD3), QUIRK_G752_KEYBOARD },
	{ HID_USB_DEVICE(USB_VENDOR_ID_ASUSTEK,
		USB_DEVICE_ID_ASUSTEK_FX503VD_KEYBOARD),
	  QUIRK_USE_KBD_BACKLIGHT },
	{ HID_USB_DEVICE(USB_VENDOR_ID_ASUSTEK,
	    USB_DEVICE_ID_ASUSTEK_ROG_NKEY_KEYBOARD),
	  QUIRK_USE_KBD_BACKLIGHT | QUIRK_ROG_NKEY_KEYBOARD },
	{ HID_USB_DEVICE(USB_VENDOR_ID_ASUSTEK,
	    USB_DEVICE_ID_ASUSTEK_ROG_NKEY_KEYBOARD2),
	  QUIRK_USE_KBD_BACKLIGHT | QUIRK_ROG_NKEY_KEYBOARD | QUIRK_HID_FN_LOCK },
	{ HID_USB_DEVICE(USB_VENDOR_ID_ASUSTEK,
	    USB_DEVICE_ID_ASUSTEK_ROG_Z13_LIGHTBAR),
	  QUIRK_USE_KBD_BACKLIGHT | QUIRK_ROG_NKEY_KEYBOARD },
	{ HID_USB_DEVICE(USB_VENDOR_ID_ASUSTEK,
	    USB_DEVICE_ID_ASUSTEK_ROG_NKEY_ALLY),
	  QUIRK_USE_KBD_BACKLIGHT | QUIRK_ROG_NKEY_KEYBOARD | QUIRK_ROG_ALLY_XPAD},
	{ HID_USB_DEVICE(USB_VENDOR_ID_ASUSTEK,
	    USB_DEVICE_ID_ASUSTEK_ROG_NKEY_ALLY_X),
	  QUIRK_USE_KBD_BACKLIGHT | QUIRK_ROG_NKEY_KEYBOARD | QUIRK_ROG_ALLY_XPAD },
	{ HID_USB_DEVICE(USB_VENDOR_ID_ASUSTEK,
	    USB_DEVICE_ID_ASUSTEK_XGM_2022),
	},
	{ HID_USB_DEVICE(USB_VENDOR_ID_ASUSTEK,
	    USB_DEVICE_ID_ASUSTEK_XGM_2023),
	},
	{ HID_USB_DEVICE(USB_VENDOR_ID_ASUSTEK,
	    USB_DEVICE_ID_ASUSTEK_ROG_CLAYMORE_II_KEYBOARD),
	  QUIRK_ROG_CLAYMORE_II_KEYBOARD },
	{ HID_USB_DEVICE(USB_VENDOR_ID_ASUSTEK,
		USB_DEVICE_ID_ASUSTEK_T100TA_KEYBOARD),
	  QUIRK_T100_KEYBOARD | QUIRK_NO_CONSUMER_USAGES },
	{ HID_USB_DEVICE(USB_VENDOR_ID_ASUSTEK,
		USB_DEVICE_ID_ASUSTEK_T100TAF_KEYBOARD),
	  QUIRK_T100_KEYBOARD | QUIRK_NO_CONSUMER_USAGES },
	{ HID_USB_DEVICE(USB_VENDOR_ID_CHICONY, USB_DEVICE_ID_ASUS_AK1D) },
	{ HID_USB_DEVICE(USB_VENDOR_ID_TURBOX, USB_DEVICE_ID_ASUS_MD_5110) },
	{ HID_USB_DEVICE(USB_VENDOR_ID_JESS, USB_DEVICE_ID_ASUS_MD_5112) },
	{ HID_BLUETOOTH_DEVICE(USB_VENDOR_ID_ASUSTEK,
		USB_DEVICE_ID_ASUSTEK_T100CHI_KEYBOARD), QUIRK_T100CHI },
	{ HID_USB_DEVICE(USB_VENDOR_ID_ITE, USB_DEVICE_ID_ITE_MEDION_E1239T),
		QUIRK_MEDION_E1239T },
	/*
	 * Note bind to the HID_GROUP_GENERIC group, so that we only bind to the keyboard
	 * part, while letting hid-multitouch.c handle the touchpad.
	 */
	{ HID_DEVICE(BUS_USB, HID_GROUP_GENERIC,
		USB_VENDOR_ID_ASUSTEK, USB_DEVICE_ID_ASUSTEK_ROG_Z13_FOLIO),
	  QUIRK_USE_KBD_BACKLIGHT | QUIRK_ROG_NKEY_KEYBOARD },
	{ HID_DEVICE(BUS_USB, HID_GROUP_GENERIC,
		USB_VENDOR_ID_ASUSTEK, USB_DEVICE_ID_ASUSTEK_T101HA_KEYBOARD) },
	{ }
};
MODULE_DEVICE_TABLE(hid, asus_devices);

static struct hid_driver asus_driver = {
	.name			= "asus",
	.id_table		= asus_devices,
	.report_fixup		= asus_report_fixup,
	.probe                  = asus_probe,
	.remove			= asus_remove,
	.input_mapping          = asus_input_mapping,
	.input_configured       = asus_input_configured,
	.reset_resume           = pm_ptr(asus_reset_resume),
	.resume			= pm_ptr(asus_resume),
	.suspend		= pm_ptr(asus_suspend),
	.event			= asus_event,
	.raw_event		= asus_raw_event
};
module_hid_driver(asus_driver);

MODULE_IMPORT_NS("ASUS_WMI");
MODULE_LICENSE("GPL");