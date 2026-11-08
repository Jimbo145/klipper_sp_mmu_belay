from klippy import chelper


DIRECTION_UPDATE_INTERVAL = 0.1
POSITION_TIME_DIFF = 0.3


class SpMmuBelay:
    def __init__(self, config):
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.name = config.get_name().split()[1]
        self.stepper_name = config.get('manual_stepper', 'sp_motor')
        self.extruder_name = config.get('extruder', 'extruder')
        self.multiplier_high = config.getfloat('multiplier_high', 1.05, minval=1.0)
        self.multiplier_low = config.getfloat('multiplier_low', 0.95, above=0.0, maxval=1.0)
        self.enabled = False
        self.tight = False
        self.slack = False
        self.buffer_state = None
        self.direction = True
        self.multiplier = 1.0
        self.fault = None
        self.manual = self.stepper = self.toolhead = self.extruder = None
        self.original_kinematics = self.belay_kinematics = None
        self.base_rotation_distance = None
        self.timer = self.reactor.register_timer(self.update_direction)
        self.gcode = self.printer.lookup_object('gcode')
        buttons = self.printer.load_object(config, 'buttons')
        self.tight_pin = config.get('tight_pin', None)
        if self.tight_pin is not None:
            buttons.register_buttons([self.tight_pin], self.tight_callback)
        else:
            self.buffer_state = 'tight'
        buttons.register_buttons([config.get('slack_pin')], self.slack_callback)
        self.printer.register_event_handler('klippy:connect', self.handle_connect)
        for command, handler in (
            ('SP_BELAY_ENABLE', self.cmd_ENABLE),
            ('SP_BELAY_DISABLE', self.cmd_DISABLE),
            ('QUERY_SP_BELAY', self.cmd_QUERY),
        ):
            self.gcode.register_mux_command(command, 'BELAY', self.name, handler)

    def handle_connect(self):
        self.toolhead = self.printer.lookup_object('toolhead')
        self.manual = self.printer.lookup_object('manual_stepper ' + self.stepper_name)
        self.extruder = self.printer.lookup_object(self.extruder_name)
        if len(self.manual.steppers) != 1:
            raise self.printer.config_error('SP belay requires exactly one manual stepper')
        self.stepper = self.manual.steppers[0]
        ffi_main, ffi_lib = chelper.get_ffi()
        self.belay_kinematics = ffi_main.gc(
            ffi_lib.extruder_stepper_alloc(), ffi_lib.extruder_stepper_free)

    def enable(self):
        if self.enabled:
            return
        if self.tight and self.slack:
            raise self.printer.command_error('SP belay: both buffer switches are triggered')
        if self.toolhead.get_extruder() is not self.extruder:
            raise self.printer.command_error('SP belay: configured extruder is not active')
        if self.manual.axis_gcode_id is not None:
            raise self.printer.command_error('SP belay: manual stepper is registered as a G-code axis')
        if self.stepper.get_trapq() != self.manual.trapq:
            raise self.printer.command_error('SP belay: manual stepper motion queue is already in use')
        if self.tight_pin is not None and not self.tight and not self.slack:
            self.buffer_state = None
        self.toolhead.flush_step_generation()
        self.base_rotation_distance = self.stepper.get_rotation_distance()[0]
        self.original_kinematics = self.stepper.get_stepper_kinematics()
        self.stepper.set_stepper_kinematics(self.belay_kinematics)
        self.stepper.set_position([self.extruder.last_position, 0., 0.])
        self.stepper.set_trapq(self.extruder.get_trapq())
        self.printer.lookup_object('motion_queuing').check_step_generation_scan_windows()
        self.enabled = True
        self.fault = None
        self.update_direction(self.reactor.monotonic())
        self.update_multiplier()
        self.reactor.update_timer(self.timer, self.reactor.NOW)

    def disable(self):
        if not self.enabled:
            return
        self.reactor.update_timer(self.timer, self.reactor.NEVER)
        self.toolhead.flush_step_generation()
        self.stepper.set_trapq(None)
        self.stepper.set_rotation_distance(self.base_rotation_distance)
        self.stepper.set_stepper_kinematics(self.original_kinematics)
        self.stepper.set_position([self.manual.commanded_pos, 0., 0.])
        self.stepper.set_trapq(self.manual.trapq)
        self.printer.lookup_object('motion_queuing').check_step_generation_scan_windows()
        self.multiplier = 1.0
        self.enabled = False

    def tight_callback(self, eventtime, state):
        self.tight = bool(state)
        self.update_sensor_state()

    def slack_callback(self, eventtime, state):
        self.slack = bool(state)
        self.update_sensor_state()

    def update_sensor_state(self):
        if self.tight and self.slack:
            self.fault = 'both buffer switches triggered'
            if self.enabled:
                self.disable()
                self.manual.do_enable(False)
                self.gcode.respond_info('SP belay: both buffer switches triggered; pausing')
                self.gcode.run_script('PAUSE')
            return
        if self.tight_pin is None:
            self.buffer_state = 'slack' if self.slack else 'tight'
        elif self.tight:
            self.buffer_state = 'tight'
        elif self.slack:
            self.buffer_state = 'slack'
        if self.enabled:
            self.update_multiplier()

    def update_multiplier(self):
        if self.buffer_state is None:
            multiplier = 1.0
        elif (self.buffer_state == 'tight') == self.direction:
            multiplier = self.multiplier_high
        else:
            multiplier = self.multiplier_low
        if multiplier != self.multiplier:
            self.toolhead.flush_step_generation()
            self.stepper.set_rotation_distance(self.base_rotation_distance / multiplier)
            self.multiplier = multiplier

    def update_direction(self, eventtime):
        if not self.enabled:
            return self.reactor.NEVER
        if self.toolhead.get_extruder() is not self.extruder:
            self.fault = 'active extruder changed'
            self.disable()
            self.manual.do_enable(False)
            self.gcode.respond_info('SP belay: active extruder changed; pausing')
            self.gcode.run_script('PAUSE')
            return self.reactor.NEVER
        print_time = self.printer.lookup_object('mcu').estimated_print_time(eventtime)
        current = self.extruder.find_past_position(print_time)
        previous = self.extruder.find_past_position(max(0., print_time - POSITION_TIME_DIFF))
        direction = current >= previous
        if direction != self.direction:
            self.direction = direction
            self.update_multiplier()
        return eventtime + DIRECTION_UPDATE_INTERVAL

    def cmd_ENABLE(self, gcmd):
        self.enable()
        gcmd.respond_info('SP belay enabled')

    def cmd_DISABLE(self, gcmd):
        self.disable()
        gcmd.respond_info('SP belay disabled')

    def cmd_QUERY(self, gcmd):
        gcmd.respond_info('SP belay %s: %s' % (self.name, self.get_status(self.reactor.monotonic())))

    def get_status(self, eventtime):
        return {'enabled': self.enabled, 'tight': self.tight, 'slack': self.slack,
                'buffer_state': self.buffer_state, 'direction': 'forward' if self.direction else 'reverse',
                'multiplier': self.multiplier, 'fault': self.fault}


def load_config_prefix(config):
    return SpMmuBelay(config)