# SP MMU Belay

A Klipper/Kalico component for running an SP MMU manual stepper as a filament
follower using one or two switches on an Armored Turtle TurtleNeck buffer. It
adapts the direction-aware multiplier idea from [Kalico Belay](https://github.com/KalicoCrew/kalico/blob/main/klippy/extras/belay.py)
to SP MMU's existing `[manual_stepper]`; it does not create a second stepper
or change the SP MMU loading code.

**Experimental: do not leave the printer unattended until the complete
hardware and tool-change sequence has been tested.** This does not detect a
jam, broken filament, disconnected switch, lost motion, or a buffer stuck at
one end. It is not a substitute for runout or clog detection. Klipper queues
motion ahead of sensor feedback; corrections and pauses are not instantaneous.

## How it works

When enabled, the existing manual stepper is attached to the configured
extruder's motion queue. Its original manual motion queue, solver and rotation
distance are restored on disable. The follower has no pressure advance of its
own; it follows nominal extruder motion in both directions. The configured
extruder must be active. A different active extruder or both switches triggered
at once disables the follower, releases motor power and requests `PAUSE`.

The switches identify the tight (needs more feed) and slack (needs less feed)
ends of buffer travel. While moving forward, tight uses `multiplier_high` and
slack uses `multiplier_low`; during retraction they are reversed, following
Belay's direction-aware behavior. The most recently triggered end is retained
between switches. Until either end is triggered after enabling, the multiplier
is 1.0. If `tight_pin` is omitted, the slack switch instead acts as a single
Belay sensor: triggered means slack, and released selects the tight control
state. The latter is inferred and does not indicate that the buffer is actually
at the tight end. Single-switch mode starts with the high forward multiplier
when the slack switch is released; it has no tight-end detection or two-switch
fault detection. Direction is sampled every 100 ms using a 300 ms
extruder-position history. Multipliers adjust rotation distance, not extruder flow.

This is intended for the Kalico version whose manual stepper exposes `trapq`,
`steppers`, `commanded_pos` and `axis_gcode_id`, as on the target machine. It
has not been verified against other Klipper or Kalico versions. It does not
apply SP MMU's per-lane `lane_em`; calibrate the manual stepper's rotation
distance and start with lane multipliers at 1.0.

## Install

Run `bash install.sh` from this repository. Set `KLIPPER_PATH` if Klipper is
not at `~/klipper`. The installer links the component into Kalico's plugins
directory when present, otherwise Klipper's extras directory. It will not
overwrite an existing file or restart Klipper. Remove that symlink to uninstall.

Copy the contents of [examples/sp_mmu_belay.cfg](examples/sp_mmu_belay.cfg)
into a separate printer configuration file, replace the `slack_pin` placeholder,
and include it in `printer.cfg`. For a second switch, uncomment and replace the
`tight_pin` placeholder. Do not include the example with placeholder pins.
`tight_pin` is the optional switch at the end where the buffer needs more feed;
`slack_pin` is the required switch at the opposite end. The `^!` example assumes a
normally open switch connected to ground with an MCU pullup: triggered must
read `true`. Check the board's voltage, pinout and switch wiring first. The
sensor state defaults to open until Klipper receives switch events; do not
enable immediately at startup without verifying the configured switches.

Restart Klipper only after the configuration and lifecycle hooks are complete.
The component is disabled at startup and does not automatically engage a lane.

## Integrate With SP MMU

The example's `SP_BELAY_START` selects the saved loaded lane using SP MMU's
`_SP_LANE_SELECT` before attaching the stepper. It refuses to start without
an enabled, fused MMU, a valid loaded lane, or on a guest lane. `SP_BELAY_STOP`
waits for queued motion, detaches the follower, and homes/releases the selector.
The commands depend on SP MMU 0.9.4 configuration / 1.0.5 code; review them
when using a different version.

1. In your print-start sequence, run `SP_BELAY_START` **after** SP MMU has
   initialized, loaded the selected lane, and finished any load or purge moves.
   Include the no-tool-change path too.
2. Add `SP_BELAY_STOP` to the existing `[gcode_macro _SP_BEFORE_CHANGE]` in
   SP MMU's configuration. Add `SP_BELAY_START` to its existing
   `[gcode_macro _SP_AFTER_CHANGE]`, only after a successful change. These
   sections already exist; do not define duplicates. Confirm the hooks run on
   your print-start and runout paths too. The after-change hook can also run
   outside a print: guard `SP_BELAY_START` with
   `{% if printer['gcode_macro _SP_VARS_RUNTIME'].fused|int == 1 and
   printer['gcode_macro _SP_VARS_RUNTIME'].exit_code|int == 0 %}` and a
   matching `{% endif %}`.
3. Add `SP_BELAY_STOP` before SP MMU reset/home in print-end and cancel paths.
   Also stop before any manual load, unload, lane selection, homing, calibration,
   `SP_INIT`, or direct `MANUAL_STEPPER` command. Do not execute manual stepper
   moves or change the active extruder while the follower is enabled. Re-enable
   only after the operation has completed successfully.
4. Ensure the real print-start, print-end and cancel macros actually call those
   hooks. This component does not infer a print lifecycle or wrap existing
   macros. Never rely on `_SP_AFTER_CHANGE` alone to start the follower when
   the initial lane is already loaded.

The raw commands are `SP_BELAY_ENABLE BELAY=turtleneck`,
`SP_BELAY_DISABLE BELAY=turtleneck`, and `QUERY_SP_BELAY BELAY=turtleneck`.
The raw enable command does **not** select a lane or check SP MMU state; prefer
the example macros. Status is available as `printer['sp_mmu_belay turtleneck']`
with `enabled`, `tight`, `slack`, `buffer_state`, `direction`, `multiplier`,
and `fault`. A fault remains visible until a successful re-enable. `PAUSE`
must be configured; it does not automatically home the selector. Inspect and
recover the hardware before resuming, then explicitly re-enable if appropriate.

## Commissioning

1. With the drive disengaged, query the component and manually actuate each
   configured switch. Confirm only the intended field becomes true and returns
   false when released. In single-switch mode, `tight` remains false while
   `buffer_state` changes between `slack` and inferred `tight`. Swap pins or
   inversion if needed.
2. Verify the selected lane, motor direction, rotation distance and gear
   engagement with filament unloaded. Run short, slow extrusion and retraction
   moves while observing the follower. Use `SP_BELAY_STOP` before manual moves.
3. Load filament and run short, slow extrusions while watching buffer travel.
   With a tight switch, forward feed should speed up at the tight end; at the
   slack end it should slow down. Without a tight switch, feed speeds up when
   the slack switch releases. Verify reverse behavior with short retractions. Stop immediately
   if it moves toward a hard stop, binds, or the motor slips.
4. Test successful and failed tool changes, runout, pause/resume, print end and
   cancel before attempting a supervised print. Tune multipliers conservatively.

Run the simulated tests with `python3 -m unittest discover -s tests -v`. They do
not exercise real MCU timing or hardware.

## License

GPL-3.0. The direction-aware control is derived from Kalico Belay by
Ryan Ghosh, distributed under GPLv3. TurtleNeck is an Armored Turtle project.