from PyQt4 import QtCore
from collections import namedtuple
import midiutils, string

MidiSource = namedtuple('MidiSource', 'client port addr')
datetime_fmt = 'yyyyMMddHHmmsszzz'

def time_short_repr(secs):
    if secs < 60:
        txt = '{} seconds'.format(secs)
    elif secs < 3600:
        t = secs//60
        txt = '{} minute{}'.format(t, 's' if t > 1 else '')
    elif secs < 86400:
        t = secs//3600
        txt = '{} hour{}'.format(t, 's' if t > 1 else '')
    else:
        t = secs//86400
        txt = '{} day{}'.format(t, 's' if t > 1 else '')
    return txt


class ConnectionEvent(object):
    def __init__(self, source, dest, state):
        self.source = MidiSource(*source)
        self.dest = MidiSource(*dest)
        self.state = state
        self.type = midiutils.NONE

class MidiData(QtCore.QObject):
    __slots__ = 'event', 'time', 'source', 'enabled'
    play = QtCore.pyqtSignal()
    def __init__(self, event, time=0, source=None, enabled=True, visible=True):
        QtCore.QObject.__init__(self)
        self.event = event
        self.time = time
        self.time_ms = time/(10.**6)
        self.source = source
        self.enabled = enabled
        self.visible = visible
        self.timer = QtCore.QTimer()
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.event_play)
        self.timer_valid = True

    def event_play(self):
        if self.enabled and self.visible:
            self.play.emit()

    def set_timer(self, start=0, ratio=1):
        if self.time_ms-start < 0:
            self.timer_valid = False
            return
        self.timer_valid = True
        self.timer.setInterval((self.time_ms-start)/ratio)

    def timer_start(self):
        if self.timer_valid:
            self.timer.start()

    def serialize(self):
        if isinstance(self.event, ConnectionEvent):
            event = {
                     'type': 'CONN', 
                     'source': tuple(self.event.source), 
                     'dest': tuple(self.event.dest), 
                     'state': self.event.state, 
                     }
        else:
            event = {'type': str(self.event.type)}
            for attr in ['source', 'dest', 'port', 'backend', 'channel', 'data1', 'data2', 'sysex']:
                event[attr] = getattr(self.event, attr)
        data = {
                'time': self.time, 
                'source': dict(self.source._asdict()), 
                'enabled': self.enabled, 
                'visible': self.visible, 
                'event': event, 
                }
        return data

    @classmethod
    def unserialize(cls, data):
        event_dict = data.pop('event')
        event_type_str = event_dict.pop('type')
        if event_type_str == 'CONN':
            event = ConnectionEvent(**event_dict)
        else:
            event_type = getattr(midiutils, event_type_str)
            event = midiutils.MidiEvent(event_type, **event_dict)
        source = MidiSource(**data.pop('source'))
        return cls(event, source=source, **data)

    def __iter__(self):
        for field in self.__slots__:
            yield getattr(self, field)


class MidiStream(QtCore.QObject):
    def __init__(self, data=None, end=None):
        QtCore.QObject.__init__(self)
        self.data = data if data is not None else []
        self.end = end
        if end:
            self.start = self.end.addSecs(-self.data[-1].time*10**-9)

    def append(self, event, time, source=None):
        self.data.append(MidiData(event, time, source))

    def close(self, last_event_limit):
        now = QtCore.QDateTime.currentDateTime()
        self.end = now.addSecs(-last_event_limit)
        self.start = self.end.addSecs(-self.data[-1].time*10**-9)

    def quantize_start(self):
        start = self.data[0].time
        for event in self.data:
            event.time = event.time-start

    def serialize(self):
        return {
                'data': [data.serialize() for data in self.data], 
                'end': str(self.end.toString(datetime_fmt))
                }

    @classmethod
    def unserialize(cls, attr):
        return cls(data=[MidiData.unserialize(data) for data in attr['data']], end=QtCore.QDateTime.fromString(attr['end'], datetime_fmt))

    @property
    def note_count(self):
        return len([e for e in self.data if isinstance(e, midiutils.MidiEvent) and (e.event.type == midiutils.NOTEON and e.event.velocity != 0)])

#    @property
#    def nlength(self):
#        return sorted([e.time for e in self.data])[-1]

    @property
    def slength(self):
        return int(round(self.data[-1].time/(10.0**9)))

    def __iter__(self):
        for e in self.data:
            yield e

    def __getitem__(self, index):
        return self.data[index]

    def __len__(self):
        return len(self.data)

    def __repr__(self):
        notes = self.note_count
        secs = self.slength
        mins = self.slength//60
        if mins: secs = self.slength%60
        delta = self.end.secsTo(QtCore.QDateTime.currentDateTime())
        return '{e} events {n}in {m}{s} ({t} ago)'.format(
                                                   e = len(self.data),
                                                   n = '({} note{}) '.format(notes, 's' if notes > 1 else '') if self.note_count else '', 
                                                   m = '{}\''.format(mins) if mins else '', 
                                                   s = '{:02d}"'.format(secs) if mins else '{}"'.format(secs), 
                                                   t = time_short_repr(delta), 
                                                   )

class Signal(object):
    def __init__(self):
        self.subscribers = []

    def emit(self, *args, **kwargs):
        for sub in self.subscribers:
            sub(*args, **kwargs)

    def connect(self, func):
        self.subscribers.append(func)

    def disconnect(self, func=None):
        if func is None:
            del self.subscribers[:]
            return
        try:
            self.subscribers.remove(func)
        except ValueError:
            print 'No {} function in signal {}'.format(func, self)

class SettingsGroup(object):
    def __init__(self, settings, name=None):
        self._settings = settings
        self._group = settings.group()
        self._signals = {}
        for k in settings.childKeys():
            value = settings.value(k).toPyObject()
            if isinstance(value, QtCore.QString):
                value = str(value)
                if value == 'true':
                    value = True
                elif value == 'false':
                    value = False
                elif self._is_int(value):
                    value = int(value)
                else:
                    try:
                        value = float(value)
                    except:
                        pass
            setattr(self, self._decode(str(k)), value)
        if len(self._group):
            for g in settings.childGroups():
                settings.beginGroup(g)
                setattr(self, 'g{}'.format(self._decode(g)), SettingsGroup(settings))
                settings.endGroup()
        self._done = True

    def createGroup(self, name):
        self._settings.beginGroup(self._group)
        self._settings.beginGroup(name)
        gname = 'g{}'.format(self._decode(name))
        setattr(self, gname, SettingsGroup(self._settings))
        self._settings.endGroup()
        self._settings.endGroup()

    def _decode(self, txt):
        txt = txt.replace('_', '__')
        txt = txt.replace(' ', '_')
        return txt

    def _encode(self, txt):
        txt = txt.replace('__', '::')
        txt = txt.replace('_', ' ')
        txt = txt.replace('::', '_')
        return txt

    def _is_int(self, value):
        try:
            int(value)
            return True
        except:
            return False

    def __setattr__(self, name, value):
        if '_done' in self.__dict__.keys():
            if not isinstance(value, SettingsGroup):
                dname = self._encode(name)
                if len(self._group):
                    self._settings.beginGroup(self._group)
                    self._settings.setValue(dname, value)
                    self._settings.endGroup()
                else:
                    self._settings.setValue(dname, value)
                if name in self._signals:
                    self._signals[name].emit(value)
                super(SettingsGroup, self).__setattr__(name, value)
            else:
                super(SettingsGroup, self).__setattr__(name, value)
        else:
            super(SettingsGroup, self).__setattr__(name, value)

    def __getattr__(self, name):
        def save_func(value):
            self._settings.beginGroup(self._group)
            self._settings.setValue(self._encode(name[4:]), value)
            self._settings.endGroup()
            setattr(self, name[4:], value)
            return value
        if name.startswith('set_'):
            obj = type('setter', (object, ), {})()
            obj.__class__.__call__ = lambda x, y=None: setattr(self, name[4:], y)
            return obj
        if name.startswith('changed_'):
            try:
                getattr(self, name[8:])
                signal = self._signals.setdefault(name[8:], Signal())
                return signal
            except:
                raise Exception('Value {} does not exist'.format(name[8:]))
        if not name.startswith('get_'):
            return
        try:
            orig = super(SettingsGroup, self).__getattribute__(name[4:])
            if isinstance(orig, bool):
                obj = type(type(orig).__name__, (object,), {'value': orig})()
                obj.__class__.__call__ = lambda x,  y=None, save=False:orig
                obj.__class__.__len__ = lambda x: orig
                obj.__class__.__eq__ = lambda x, y: True if x.value==y else False
            else:
                obj = type(type(orig).__name__, (type(orig), ), {})(orig)
                obj.__class__.__call__ = lambda x, y=None, save=False:x
            return obj
        except Exception:
            print 'Setting {} for group {} not found'.format(name[4:], self._group)
            obj = type('obj', (object,), {})()
            obj.__class__.__call__ = lambda x, y=None, save=False:y if not save else save_func(y)
            return obj

class SettingsObj(object):
    def __init__(self, settings):
        self._settings = settings
        self._sdata = []
        self._load()
        self._done = True

    def _load(self):
        for d in self._sdata:
            delattr(self, d)
        self._sdata = []
        self._settings.sync()
        self.gGeneral = SettingsGroup(self._settings)
        self._sdata.append('gGeneral')
        for g in self._settings.childGroups():
            self._settings.beginGroup(g)
            gname = 'g{}'.format(self._decode(g))
            self._sdata.append(gname)
            setattr(self, gname, SettingsGroup(self._settings))
            self._settings.endGroup()

    def __getattr__(self, name):
        if not (name.startswith('g') and name[1] in string.ascii_uppercase):
            raise AttributeError
        name = name[1:]
        self._settings.beginGroup(name)
        gname = 'g{}'.format(self._decode(name))
        self._sdata.append(gname)
        new_group = SettingsGroup(self._settings)
        setattr(self, gname, new_group)
        self._settings.endGroup()
        return new_group

    def sync(self):
        self._settings.sync()

    def createGroup(self, name):
        self._settings.beginGroup(name)
        gname = 'g{}'.format(self._decode(name))
        self._sdata.append(gname)
        setattr(self, gname, SettingsGroup(self._settings))
        self._settings.endGroup()

    def _decode(self, txt):
        txt = txt.replace('_', '__')
        txt = txt.replace(' ', '_')
        return txt


