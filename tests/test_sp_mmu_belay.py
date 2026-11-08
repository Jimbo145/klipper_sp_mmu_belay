import importlib.util
import pathlib
import sys
import types
import unittest
from unittest import mock


sys.modules.setdefault('klippy', types.SimpleNamespace(chelper=mock.Mock()))
spec = importlib.util.spec_from_file_location(
    'sp_mmu_belay', pathlib.Path(__file__).resolve().parents[1] / 'sp_mmu_belay.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class Config:
    def __init__(self, tight_pin='^!tight'):
        self.tight_pin = tight_pin
        self.printer = mock.Mock()
        self.reactor = mock.Mock(NOW=0., NEVER=999999.)
        self.reactor.monotonic.return_value = 10.
        self.printer.get_reactor.return_value = self.reactor
        self.gcode = mock.Mock()
        self.buttons = mock.Mock()
        self.stepper = mock.Mock()
        self.stepper.get_rotation_distance.return_value = (18.3, 3200)
        self.stepper.get_stepper_kinematics.return_value = 'manual-solver'
        self.stepper.get_trapq.return_value = 'manual-queue'
        self.manual = mock.Mock(steppers=[self.stepper], axis_gcode_id=None,
                                commanded_pos=12., trapq='manual-queue')
        self.extruder = mock.Mock(last_position=42.)
        self.extruder.get_trapq.return_value = 'extruder-queue'
        self.extruder.find_past_position.return_value = 0.
        self.toolhead = mock.Mock()
        self.toolhead.get_extruder.return_value = self.extruder
        self.mcu = mock.Mock()
        self.mcu.estimated_print_time.return_value = 10.
        objects = {'gcode': self.gcode, 'toolhead': self.toolhead,
                   'manual_stepper sp_motor': self.manual, 'extruder': self.extruder,
                   'mcu': self.mcu, 'motion_queuing': mock.Mock()}
        self.printer.lookup_object.side_effect = lambda name: objects[name]
        self.printer.load_object.return_value = self.buttons
        self.printer.command_error = ValueError
        self.printer.config_error = ValueError

    def get_printer(self):
        return self.printer

    def get_name(self):
        return 'sp_mmu_belay test'

    def get(self, name, default=None):
        return {'tight_pin': self.tight_pin, 'slack_pin': '^!slack'}.get(name, default)

    def getfloat(self, name, default, **kwargs):
        return default


class BelayTests(unittest.TestCase):
    def setUp(self):
        self.config = Config()
        module.chelper.get_ffi.return_value = (mock.Mock(), mock.Mock())
        self.belay = module.SpMmuBelay(self.config)
        self.belay.handle_connect()

    def test_enable_and_restore_manual_queue(self):
        self.belay.enable()
        self.assertTrue(self.belay.enabled)
        self.config.stepper.set_position.assert_called_with([42., 0., 0.])
        self.config.stepper.set_trapq.assert_called_with('extruder-queue')
        self.belay.disable()
        self.assertFalse(self.belay.enabled)
        self.config.stepper.set_stepper_kinematics.assert_called_with('manual-solver')
        self.config.stepper.set_position.assert_called_with([12., 0., 0.])
        self.config.stepper.set_trapq.assert_called_with('manual-queue')
        self.config.stepper.set_rotation_distance.assert_called_with(18.3)

    def test_buffer_hysteresis_and_retraction(self):
        self.belay.enable()
        self.belay.tight_callback(0, True)
        self.assertEqual(self.belay.multiplier, 1.05)
        self.config.stepper.set_rotation_distance.assert_called_with(18.3 / 1.05)
        self.belay.tight_callback(1, False)
        self.assertEqual(self.belay.multiplier, 1.05)
        self.config.extruder.find_past_position.side_effect = [1., 2.]
        self.belay.update_direction(10.)
        self.assertEqual(self.belay.multiplier, 0.95)
        self.belay.slack_callback(2, True)
        self.assertEqual(self.belay.multiplier, 1.05)

    def test_both_switches_pause_and_restore(self):
        self.belay.enable()
        self.belay.tight_callback(0, True)
        self.belay.slack_callback(0, True)
        self.assertFalse(self.belay.enabled)
        self.assertEqual(self.belay.fault, 'both buffer switches triggered')
        self.config.gcode.run_script.assert_called_once_with('PAUSE')
        self.config.manual.do_enable.assert_called_once_with(False)
        self.config.stepper.set_trapq.assert_called_with('manual-queue')
        with self.assertRaises(ValueError):
            self.belay.enable()

    def test_changed_extruder_pauses(self):
        self.belay.enable()
        self.config.toolhead.get_extruder.return_value = object()
        self.assertEqual(self.belay.update_direction(1.), self.config.reactor.NEVER)
        self.assertFalse(self.belay.enabled)
        self.config.gcode.run_script.assert_called_once_with('PAUSE')

    def test_rejects_busy_stepper_and_wrong_extruder(self):
        self.config.manual.axis_gcode_id = 'P'
        with self.assertRaises(ValueError):
            self.belay.enable()
        self.config.manual.axis_gcode_id = None
        self.config.toolhead.get_extruder.return_value = object()
        with self.assertRaises(ValueError):
            self.belay.enable()
        self.config.stepper.set_stepper_kinematics.assert_not_called()

    def test_disable_then_enable_clears_stale_buffer_state(self):
        self.belay.tight_callback(0, True)
        self.belay.tight_callback(0, False)
        self.belay.enable()
        self.assertIsNone(self.belay.buffer_state)
        self.assertEqual(self.belay.multiplier, 1.)

    def test_enable_uses_current_reverse_direction(self):
        self.belay.tight_callback(0, True)
        self.config.extruder.find_past_position.side_effect = [1., 2.]
        self.belay.enable()
        self.assertFalse(self.belay.direction)
        self.assertEqual(self.belay.multiplier, 0.95)

    def test_rejects_stepper_with_foreign_queue(self):
        self.config.stepper.get_trapq.return_value = 'foreign-queue'
        with self.assertRaises(ValueError):
            self.belay.enable()
        self.config.stepper.set_stepper_kinematics.assert_not_called()

    def test_single_switch_tracks_both_states_and_direction(self):
        config = Config(tight_pin=None)
        belay = module.SpMmuBelay(config)
        config.buttons.register_buttons.assert_called_once_with(['^!slack'], belay.slack_callback)
        belay.handle_connect()
        belay.enable()
        self.assertEqual((belay.buffer_state, belay.multiplier), ('tight', 1.05))
        belay.slack_callback(0, True)
        self.assertEqual((belay.buffer_state, belay.multiplier), ('slack', 0.95))
        config.extruder.find_past_position.side_effect = [1., 2.]
        belay.update_direction(10.)
        self.assertEqual(belay.multiplier, 1.05)
        belay.slack_callback(1, False)
        self.assertEqual((belay.buffer_state, belay.multiplier), ('tight', 0.95))
        belay.disable()
        config.extruder.find_past_position.side_effect = None
        belay.enable()
        self.assertEqual(belay.buffer_state, 'tight')


if __name__ == '__main__':
    unittest.main()