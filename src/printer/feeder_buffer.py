# feeder_buffer.py
# Klipper extra — buffer sync and jam detection for two sensor setups, chosen
# per instance via sensor_mode:
#
#   sensor_mode: dual    (default) — two-sensor buffer (advance + trailing
#                                    switches). Drop-in replacement for Belay +
#                                    BTT Smart Filament Sensor jam detection.
#                                    Supports jam detection.
#
#   sensor_mode: single             — single-switch buffer, sensor-compatible
#                                      with the Annex-Engineering Belay
#                                      hardware design. No jam detection
#                                      (a single switch can't distinguish
#                                      "stuck expanded" from "stuck
#                                      compressed" the way two switches can —
#                                      Belay itself has no jam detection
#                                      either, see credit below).
#
# AFC config-param names used throughout in dual mode so migrating to AFC
# requires only a section-header rename.
#
# Install: copy to klippy/extras/feeder_buffer.py
# Config:  [feeder_buffer <name>]  (one section per tool)
#          sensor_mode: dual | single  (optional, default dual)


class _BufferBase:
    """Shared stepper/GCode plumbing for both sensor modes."""

    def __init__(self, config):
        self.printer  = config.get_printer()
        self.name     = config.get_name().split()[-1]

        self.feeder_name = config.get('extruder_stepper')

        self.multiplier_high = config.getfloat('multiplier_high', 1.05, above=0.)
        self.multiplier_low  = config.getfloat('multiplier_low',  0.95, above=0.)
        self.debug_console   = config.getboolean('debug_console', True)

        self.state         = 'neutral'
        self.feeder        = None    # ExtruderStepper — resolved in _handle_ready
        self.base_rd       = None    # base rotation_distance (float)
        self.steps_per_rot = None
        self.last_steps    = 0
        self.fault_dist    = None    # set by dual-mode subclass only

        self.printer.register_event_handler('klippy:ready', self._handle_ready)

        # Per-instance mux GCode commands (dispatched by BUFFER=<name>)
        self.gcode = gcode = self.printer.lookup_object('gcode')
        gcode.register_mux_command(
            'QUERY_BUFFER', 'BUFFER', self.name, self.cmd_QUERY_BUFFER,
            desc="Report buffer state and rotation_distance")
        gcode.register_mux_command(
            'SET_ROTATION_FACTOR', 'BUFFER', self.name,
            self.cmd_SET_ROTATION_FACTOR,
            desc="Apply a rotation factor directly to the feeder stepper")
        gcode.register_mux_command(
            'SET_BUFFER_MULTIPLIER', 'BUFFER', self.name,
            self.cmd_SET_BUFFER_MULTIPLIER,
            desc="Live-adjust multiplier_high or multiplier_low for this buffer")

        # Default jam handler — first instance wins; user may override with
        # [gcode_macro FEEDER_BUFFER_JAM] anywhere in their config.
        try:
            gcode.register_command(
                'FEEDER_BUFFER_JAM', self._cmd_default_jam,
                desc="Called on jam detection — override with [gcode_macro FEEDER_BUFFER_JAM]")
        except Exception:
            pass  # already registered by another instance or user gcode_macro

    # -------------------------------------------------------------------------
    # Startup
    # -------------------------------------------------------------------------

    def _handle_ready(self):
        self.feeder = self.printer.lookup_object(
            'extruder_stepper ' + self.feeder_name)
        self.base_rd, self.steps_per_rot = \
            self.feeder.extruder_stepper.stepper.get_rotation_distance()

        if self.fault_dist is not None:
            self.last_steps = self._feeder_steps()
            reactor = self.printer.get_reactor()
            reactor.register_timer(self._jam_check, reactor.monotonic() + 1.)

    def _register_sensors(self, config):
        raise NotImplementedError

    def _stuck_sensor_label(self):
        raise NotImplementedError

    # -------------------------------------------------------------------------
    # Stepper helpers
    # -------------------------------------------------------------------------

    def _set_rd(self, rd):
        # NOT `.rotation_dist = rd` - that attribute doesn't exist anywhere
        # in Klipper's real stepper.py (verified: grepping the whole klippy
        # tree for `.rotation_dist` only ever matches this file). The real,
        # motion-affecting state is private (`_rotation_dist`/`_step_dist`),
        # only safely mutable via this method - it also re-anchors MCU
        # position tracking so the change doesn't cause a position jump.
        self.feeder.extruder_stepper.stepper.set_rotation_distance(rd)

    def _feeder_steps(self):
        mcu_stepper = getattr(self.feeder.extruder_stepper.stepper, '_mcu_stepper', None)
        if mcu_stepper is None:
            return 0
        return mcu_stepper.get_commanded_position()

    def _apply_state(self, new_state, factor):
        # Shared by both sensor modes' _update() — only fires on an actual
        # state change, so it can't spam the console every sensor poll.
        if new_state == self.state:
            return
        old_state = self.state
        self.state = new_state
        self._set_rd(self.base_rd * factor)
        if self.fault_dist is not None:
            self.last_steps = self._feeder_steps()
        if self.debug_console:
            self.gcode.respond_info(
                "Buffer %s: %s -> %s" % (self.name, old_state, new_state))

    # -------------------------------------------------------------------------
    # Jam detection (dual mode only — fault_dist stays None in single mode)
    # -------------------------------------------------------------------------

    def _jam_check(self, eventtime):
        if self.fault_dist is None or self.feeder is None:
            return self.printer.get_reactor().NEVER

        current_steps = self._feeder_steps()
        moved_mm = (abs(current_steps - self.last_steps)
                    * self.base_rd / self.steps_per_rot)

        if moved_mm >= self.fault_dist:
            sensor = self._stuck_sensor_label()
            self.last_steps = current_steps
            # Schedule gcode dispatch outside the timer callback
            self.printer.get_reactor().register_async_callback(
                lambda et, s=sensor: self._fire_jam(s))

        return eventtime + .5

    def _fire_jam(self, sensor):
        gcode = self.printer.lookup_object('gcode')
        gcode.run_script_from_command(
            "FEEDER_BUFFER_JAM TOOL=%s SENSOR=%s" % (self.name, sensor))

    # -------------------------------------------------------------------------
    # Default FEEDER_BUFFER_JAM handler (user-overridable)
    # -------------------------------------------------------------------------

    def _cmd_default_jam(self, gcmd):
        tool   = gcmd.get('TOOL', self.name)
        sensor = gcmd.get('SENSOR', 'unknown')
        gcmd.respond_info("Jam detected on %s (sensor: %s)" % (tool, sensor))
        self.printer.lookup_object('gcode').run_script_from_command('PAUSE')

    # -------------------------------------------------------------------------
    # GCode commands
    # -------------------------------------------------------------------------

    def cmd_QUERY_BUFFER(self, gcmd):
        rd, _ = self.feeder.extruder_stepper.stepper.get_rotation_distance()
        gcmd.respond_info(
            "Buffer %s  state=%s  rotation_distance=%.6f  base=%.6f"
            % (self.name, self.state, rd, self.base_rd))

    def cmd_SET_ROTATION_FACTOR(self, gcmd):
        factor = gcmd.get_float('FACTOR', above=0.)
        self._set_rd(self.base_rd * factor)
        rd, _ = self.feeder.extruder_stepper.stepper.get_rotation_distance()
        gcmd.respond_info(
            "Buffer %s  rotation_distance=%.6f  factor=%.4f"
            % (self.name, rd, factor))

    def cmd_SET_BUFFER_MULTIPLIER(self, gcmd):
        which  = gcmd.get('MULTIPLIER').upper()
        factor = gcmd.get_float('FACTOR', above=0.)
        if which == 'HIGH':
            self.multiplier_high = factor
        elif which == 'LOW':
            self.multiplier_low = factor
        else:
            raise gcmd.error("MULTIPLIER must be HIGH or LOW, got: %s" % which)
        gcmd.respond_info(
            "Buffer %s  multiplier_%s=%.4f" % (self.name, which.lower(), factor))


class DualSensorBuffer(_BufferBase):
    """Dual-sensor buffer: advance + trailing switches, 3-state
    machine with a neutral dead zone. Supports jam detection."""

    def __init__(self, config):
        super().__init__(config)

        self.advance_pin  = config.get('advance_pin')
        self.trailing_pin = config.get('trailing_pin')

        sensitivity = config.getint(
            'filament_error_sensitivity', 0, minval=0, maxval=10)
        self.fault_dist = (11 - sensitivity) * 10. if sensitivity > 0 else None

        self.adv_state = False   # True when advance pin active (buffer expanded)
        self.trl_state = False   # True when trailing pin active (buffer compressed)

        self._register_sensors(config)

    def _register_sensors(self, config):
        buttons = self.printer.load_object(config, 'buttons')
        buttons.register_buttons([self.advance_pin],  self._advance_handler)
        buttons.register_buttons([self.trailing_pin], self._trailing_handler)

    def _advance_handler(self, eventtime, state):
        # advance pin active → buffer is at expanded (advance) position
        self.adv_state = bool(state[0] if isinstance(state, list) else state)
        self._update(eventtime)

    def _trailing_handler(self, eventtime, state):
        # trailing pin active → buffer is at compressed (trailing) position
        self.trl_state = bool(state[0] if isinstance(state, list) else state)
        self._update(eventtime)

    def _update(self, eventtime):
        if self.feeder is None:
            return

        # Priority: trailing > advance > neutral
        if self.trl_state:
            # Buffer compressed — speed up feeder (lower rotation_distance)
            new_state, factor = 'advancing', self.multiplier_low
        elif self.adv_state:
            # Buffer expanded — slow feeder (raise rotation_distance)
            new_state, factor = 'trailing', self.multiplier_high
        else:
            new_state, factor = 'neutral', 1.

        self._apply_state(new_state, factor)

    def _stuck_sensor_label(self):
        return ('advance'  if self.adv_state else
                'trailing' if self.trl_state else 'unknown')


class BelaySensorBuffer(_BufferBase):
    # Sensor-design credit: Annex-Engineering "Belay"
    # https://github.com/Annex-Engineering/Belay
    # Original license: CC BY-NC-SA 4.0
    #
    # This class is sensor-compatible with Belay's single-switch buffer
    # hardware (same bang-bang multiplier_high/multiplier_low idea, same
    # default values). No files or code from the Belay repository are
    # copied or included — independent implementation, hardware-compatible
    # only. Belay itself has no jam/error detection (a documented future
    # idea, not implemented upstream), so neither does this class.
    """Single-sensor buffer: one switch, 2-state bang-bang machine, no
    neutral dead zone. No jam detection — see credit above."""

    def __init__(self, config):
        super().__init__(config)

        # _BufferBase defaults self.state to 'neutral', which is a real
        # dead-zone state for DualSensorBuffer but not a state this class
        # ever sets itself - a single switch only ever reports 'advancing'
        # or 'trailing'. Overriding it here so QUERY_BUFFER/debug_console
        # can't imply a third physical position exists before the first
        # sensor read arrives.
        self.state = 'unknown'

        self.sensor_pin = config.get('sensor_pin')
        self.invert     = config.getboolean('invert_sensor', False)

        self.sensor_state = False  # True when sensor active (post-inversion)

        self._register_sensors(config)

    def _register_sensors(self, config):
        buttons = self.printer.load_object(config, 'buttons')
        buttons.register_buttons([self.sensor_pin], self._sensor_handler)

    def _sensor_handler(self, eventtime, state):
        active = bool(state[0] if isinstance(state, list) else state)
        if self.invert:
            active = not active
        self.sensor_state = active
        self._update(eventtime)

    def _update(self, eventtime):
        if self.feeder is None:
            return

        if self.sensor_state:
            # Sensor active — treat as compressed (same as dual-mode trailing)
            new_state, factor = 'advancing', self.multiplier_low
        else:
            # Sensor inactive — treat as expanded (same as dual-mode advance)
            new_state, factor = 'trailing', self.multiplier_high

        self._apply_state(new_state, factor)


def load_config_prefix(config):
    mode = config.get('sensor_mode', 'dual').lower()
    if mode == 'dual':
        return DualSensorBuffer(config)
    if mode == 'single':
        return BelaySensorBuffer(config)
    raise config.error(
        "feeder_buffer: sensor_mode must be 'dual' or 'single', got: %s"
        % mode)
