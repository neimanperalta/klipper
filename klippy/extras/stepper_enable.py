# Support for enable pins on stepper motor drivers
#
# Copyright (C) 2019  Kevin O'Connor <kevin@koconnor.net>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import logging

DISABLE_STALL_TIME = 0.100

# Tracking of shared stepper enable pins
class StepperEnablePin:
    def __init__(self, mcu_enable, enable_count):
        self.mcu_enable = mcu_enable
        self.enable_count = enable_count
        self.is_dedicated = True
    def set_enable(self, print_time):
        if not self.enable_count:
            self.mcu_enable.set_digital(print_time, 1)
        self.enable_count += 1
    def set_disable(self, print_time):
        self.enable_count -= 1
        if not self.enable_count:
            self.mcu_enable.set_digital(print_time, 0)

# Enable line tracking for each stepper motor
class EnableTracking:
    def __init__(self, printer, stepper, pin):
        self.stepper = stepper
        self.callbacks = []
        self.is_enabled = False
        self.stepper.add_active_callback(self.motor_enable)
        if pin is None:
            # No enable line (stepper always enabled)
            self.enable = StepperEnablePin(None, 9999)
            self.enable.is_dedicated = False
            return
        ppins = printer.lookup_object('pins')
        pin_params = ppins.lookup_pin(pin, can_invert=True,
                                      share_type='stepper_enable')
        self.enable = pin_params.get('class')
        if self.enable is not None:
            # Shared enable line
            self.enable.is_dedicated = False
            return
        mcu_enable = pin_params['chip'].setup_pin('digital_out', pin_params)
        mcu_enable.setup_max_duration(0.)
        self.enable = pin_params['class'] = StepperEnablePin(mcu_enable, 0)
    def register_state_callback(self, callback):
        self.callbacks.append(callback)
    def motor_enable(self, print_time):
        if not self.is_enabled:
            for cb in self.callbacks:
                cb(print_time, True)
            self.enable.set_enable(print_time)
            self.is_enabled = True
    def motor_disable(self, print_time):
        if self.is_enabled:
            # Enable stepper on future stepper movement
            for cb in self.callbacks:
                cb(print_time, False)
            self.enable.set_disable(print_time)
            self.is_enabled = False
            self.stepper.add_active_callback(self.motor_enable)
    def is_motor_enabled(self):
        return self.is_enabled
    def has_dedicated_enable(self):
        return self.enable.is_dedicated

# Global stepper enable line tracking
class PrinterStepperEnable:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.enable_lines = {}
        self.printer.register_event_handler("gcode:request_restart",
                                            self._handle_request_restart)
        # Register M18/M84 commands
        gcode = self.printer.lookup_object('gcode')
        gcode.register_command("M18", self.cmd_M18)
        gcode.register_command("M84", self.cmd_M18)
        gcode.register_command("SET_STEPPER_ENABLE",
                               self.cmd_SET_STEPPER_ENABLE,
                               desc=self.cmd_SET_STEPPER_ENABLE_help)
    def register_stepper(self, stepper, pin):
        name = stepper.get_name()
        self.enable_lines[name] = EnableTracking(self.printer, stepper, pin)
    def motor_off(self):
        toolhead = self.printer.lookup_object('toolhead')
        toolhead.dwell(DISABLE_STALL_TIME)
        print_time = toolhead.get_last_move_time()
        for el in self.enable_lines.values():
            el.motor_disable(print_time)
        self.printer.send_event("stepper_enable:motor_off", print_time)
        toolhead.dwell(DISABLE_STALL_TIME)
        logging.debug('; Max time of %f', print_time)
    def motor_debug_enable(self, stepper, enable):
        toolhead = self.printer.lookup_object('toolhead')
        toolhead.dwell(DISABLE_STALL_TIME)
        print_time = toolhead.get_last_move_time()
        el = self.enable_lines[stepper]
        if enable:
            el.motor_enable(print_time)
            logging.info("%s has been manually enabled", stepper)
        else:
            el.motor_disable(print_time)
            logging.info("%s has been manually disabled", stepper)
        toolhead.dwell(DISABLE_STALL_TIME)
        logging.debug('; Max time of %f', print_time)
    def _handle_request_restart(self, print_time):
        self.motor_off()
    def cmd_M18(self, gcmd):
        # Turn off motors
        self.motor_off()
    cmd_SET_STEPPER_ENABLE_help = "Enable/disable individual stepper by name"
    def cmd_SET_STEPPER_ENABLE(self, gcmd):
        stepper_name = gcmd.get('STEPPER', None)
        if stepper_name not in self.enable_lines:
            gcmd.respond_info('SET_STEPPER_ENABLE: Invalid stepper "%s"'
                              % (stepper_name,))
            return
        stepper_enable = gcmd.get_int('ENABLE', 1)
        self.motor_debug_enable(stepper_name, stepper_enable)
    def lookup_enable(self, name):
        if name not in self.enable_lines:
            raise self.printer.config_error("Unknown stepper '%s'" % (name,))
        return self.enable_lines[name]

def load_config(config):
    return PrinterStepperEnable(config)
