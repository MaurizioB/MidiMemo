#!/usr/bin/env python2.7
# *-* coding: utf-8 *-*

import sys, re, json
from os import makedirs, path, remove
from glob import glob
from collections import namedtuple
from pyalsa import alsaseq
from PyQt4 import QtCore, QtGui, uic
import icons
from midiutils import *
from classes import ConnectionEvent, MidiSource, MidiStream, SettingsObj
import midi

clientname = 'MidiMemo'

defaults = {
            'max_rec': 20, 
            'minimum_time': 2, 
            'last_event_limit': 5, 
            'tick_res': 960, 
            }

ev_dict = {
           NOTEON: (midi.NoteOnEvent, ['data1', 'data2']), 
           NOTEOFF: (midi.NoteOffEvent, ['data1', 'data2']), 
           CTRL: (midi.ControlChangeEvent, ['data1', 'data2']), 
           PITCHBEND: (midi.PitchWheelEvent, ['data1', 'data2']), 
           AFTERTOUCH: (midi.ChannelAfterTouchEvent, ['data2']), 
           POLY_AFTERTOUCH: (midi.AfterTouchEvent, ['data1', 'data2']), 
           PROGRAM: (midi.ProgramChangeEvent, ['data2']), 
           SYSEX: (midi.SysexEvent, lambda ev: [len(ev.sysex)-1] + ev.sysex[1:-1]), 
           }

DISABLED, ENABLED, ACTIVE, EVENT, SAVED = range(5)
COL_TIME, COL_BBT, COL_SRC_ADDR, COL_SRC_CLIENT, COL_SRC_PORT, COL_EVENT_TYPE, COL_CHAN, COL_DATA1, COL_DATA2, COL_EXT = range(10)

UserRole = QtCore.Qt.UserRole
TypeRole = UserRole + 1
NameRole = UserRole + 2
IdRole = UserRole + 3
DestRole = UserRole + 4
OrdRole = UserRole + 5
EnabledRole = UserRole + 6
IgnoreRole = UserRole + 7

_path = path.dirname(path.abspath(__file__))
def _load_ui(widget, ui_path):
    return uic.loadUi(path.join(_path, ui_path), widget)

def setBold(item, bold=True):
    font = item.font()
    font.setBold(bold)
    item.setFont(font)

def setItalic(item, bold=True):
    font = item.font()
    font.setItalic(bold)
    item.setFont(font)


class AlsaMidi(QtCore.QObject):
    client_start = QtCore.pyqtSignal(object)
    client_exit = QtCore.pyqtSignal(object)
    port_start = QtCore.pyqtSignal(object)
    port_exit = QtCore.pyqtSignal(object)
    conn_register = QtCore.pyqtSignal(object, bool)
    graph_changed = QtCore.pyqtSignal()
    stopped = QtCore.pyqtSignal()
    midi_signal = QtCore.pyqtSignal(object)

    def __init__(self, main):
        QtCore.QObject.__init__(self)
        self.main = main
        self.active = False
        self.seq = alsaseq.Sequencer(clientname=clientname)
        self.keep_going = True
        input_id = self.seq.create_simple_port(name = 'MMmonitor', 
                                                     type = alsaseq.SEQ_PORT_TYPE_MIDI_GENERIC|alsaseq.SEQ_PORT_TYPE_APPLICATION, 
                                                     caps = alsaseq.SEQ_PORT_CAP_WRITE|alsaseq.SEQ_PORT_CAP_SUBS_WRITE|
                                                     alsaseq.SEQ_PORT_CAP_NO_EXPORT)
        output_id = self.seq.create_simple_port(name = 'MMplayer', 
                                                     type = alsaseq.SEQ_PORT_TYPE_MIDI_GENERIC|alsaseq.SEQ_PORT_TYPE_APPLICATION, 
                                                     caps = alsaseq.SEQ_PORT_CAP_READ|alsaseq.SEQ_PORT_CAP_SUBS_READ|
                                                     alsaseq.SEQ_PORT_CAP_SYNC_READ)
        self.seq.connect_ports((alsaseq.SEQ_CLIENT_SYSTEM, alsaseq.SEQ_PORT_SYSTEM_ANNOUNCE), (self.seq.client_id, input_id))

#        self.graph = Graph(self.seq)
        self.main.pgraph = Graph(self.seq)
        self.graph = self.main.pgraph
        self.graph.client_start.connect(self.client_start)
        self.graph.client_exit.connect(self.client_exit)
        self.graph.port_start.connect(self.port_start)
        self.graph.port_exit.connect(self.port_exit)
        self.graph.conn_register.connect(self.conn_register)
        self.id = self.seq.get_client_info()['id']
        self.input = self.graph.port_id_dict[self.id][input_id]
        self.output = self.graph.port_id_dict[self.id][output_id]

    def run(self):
        self.active = True
        while self.keep_going:
            try:
                event_list = self.seq.receive_events(timeout=1024, maxevents=1)
                for event in event_list:
                    data = event.get_data()
                    if event.type == alsaseq.SEQ_EVENT_CLIENT_START:
                        self.graph.client_created(data)
                    elif event.type == alsaseq.SEQ_EVENT_CLIENT_EXIT:
                        self.graph.client_destroyed(data)
                    elif event.type == alsaseq.SEQ_EVENT_PORT_START:
                        self.graph.port_created(data)
                    elif event.type == alsaseq.SEQ_EVENT_PORT_EXIT:
                        self.graph.port_destroyed(data)
                    elif event.type == alsaseq.SEQ_EVENT_PORT_SUBSCRIBED:
                        self.graph.conn_created(data)
                    elif event.type == alsaseq.SEQ_EVENT_PORT_UNSUBSCRIBED:
                        self.graph.conn_destroyed(data)
                    elif event.type in [alsaseq.SEQ_EVENT_NOTEON, alsaseq.SEQ_EVENT_NOTEOFF, 
                                        alsaseq.SEQ_EVENT_CONTROLLER, alsaseq.SEQ_EVENT_PITCHBEND, 
                                        alsaseq.SEQ_EVENT_CHANPRESS, alsaseq.SEQ_EVENT_KEYPRESS, 
                                        alsaseq.SEQ_EVENT_PGMCHANGE, alsaseq.SEQ_EVENT_SYSEX, 
                                        ]:
                        try:
                            newev = MidiEvent.from_alsa(event)
                            self.midi_signal.emit(newev)
#                            print newev
                        except Exception as e:
                            print 'event {} unrecognized'.format(event)
                            print e
                    elif event.type in [alsaseq.SEQ_EVENT_CLOCK, alsaseq.SEQ_EVENT_SENSING]:
                        pass
            except:
                pass
        print 'stopped'
        self.stopped.emit()

    def output_event(self, event, source=None, dest=None):
        if source is None:
            event.source = self.id, self.output.id
        if dest is None:
            event.dest = 0xfe, 0xfd
        print 'sending event {} (src: {}, dest: {})'.format(event, event.source, event.dest)
        self.seq.output_event(event)
        self.seq.drain_output()


class PlayerTimer(QtCore.QObject):
    started = QtCore.pyqtSignal()
    def __init__(self):
        QtCore.QObject.__init__(self)
        self.timer = QtCore.QElapsedTimer()
        self.delta = 0

    def start(self, delta=0):
        self.delta = delta
        self.timer.start()
        self.started.emit()

    def elapsed(self):
        return self.timer.elapsed()

    def elapsed_delta(self):
        return self.timer.elapsed() + self.delta

class SourceFilterDialog(QtGui.QDialog):
    def __init__(self, parent):
        QtGui.QDialog.__init__(self, parent)
        _load_ui(self, 'sourcefilter.ui')
        self.model = QtGui.QStandardItemModel()
        self.listview.setModel(self.model)
        self.buttonBox.button(QtGui.QDialogButtonBox.Ok).setEnabled(False)
        self.setter_edit.textChanged.connect(self.enable_ok)

    def enable_ok(self, text):
        self.buttonBox.button(QtGui.QDialogButtonBox.Ok).setEnabled(True if text else False)

    def client_has_outputs(self, graph, client_id):
        for port in graph.port_id_dict[client_id].values():
            if not port.hidden and port.is_output:
                return True
        return False

    @classmethod
    def client_id_dialog(cls, main):
        def get_id(event):
            item = self.model.item(self.listview.indexAt(event.pos()).row())
            sel_id = item.data(IdRole).toString()
            if self.setter_edit.text() == sel_id:
                self.accept()
            self.setter_edit.setText(sel_id)
        def highlight(text):
            for item in [self.model.item(row) for row in range(self.model.rowCount())]:
                setItalic(item, True if item.data(IdRole).toString()==text else False)
        self = cls(main)
        self.description_lbl.setText(
                                     'Insert a client ID or select it from the list of the existing clients.\n\n'+
                                     'Remember, Client IDs can change during runtime. If you use ALSA, you '+
                                     'should use this only for system clients (highlighted in the list).\n'+
                                     'Using this filter for JACK is useless, use name filtering instead.'
                                     )
        self.setter_lbl.setText('Client ID:')
        self.setter_edit.setValidator(QtGui.QRegExpValidator(QtCore.QRegExp('[0-9]{1,12}')))
        self.listview.mouseDoubleClickEvent = get_id
        graph = main.main.graph
        for client_id in sorted(graph.client_id_dict.keys()):
            if not self.client_has_outputs(graph, client_id): continue
            client = graph.client_id_dict[client_id]
            item = QtGui.QStandardItem('{} ({})'.format(client.name, client.id))
            if client.type == alsaseq.SEQ_KERNEL_CLIENT:
                setBold(item)
            item.setData(client.id, IdRole)
            self.model.appendRow(item)
        self.setter_edit.textChanged.connect(highlight)
        if self.exec_() and self.setter_edit.text():
            return self.setter_edit.text()
        else:
            return None

    @classmethod
    def port_id_dialog(cls, main):
        def get_id(event):
            item = self.model.item(self.listview.indexAt(event.pos()).row())
            if not item.isEnabled(): return
            sel_id = item.data(IdRole).toString()
            if self.setter_edit.text() == sel_id:
                self.accept()
            self.setter_edit.setText(sel_id)
        def highlight(text):
            for item in [self.model.item(row) for row in range(self.model.rowCount())]:
                setItalic(item, True if item.data(IdRole).toString()==text else False)
        self = cls(main)
        self.description_lbl.setText(
                                     'Insert a port ID (in "client:port" format) or select it from the list of the existing ports.\n\n'+
                                     'Remember, port IDs can change during runtime. If you use ALSA, you '+
                                     'should use this only for system ports (highlighted in the list).\n'+
                                     'Using this filter for JACK is useless, use name filtering instead.'
                                     )
        self.setter_lbl.setText('Port ID:')
        self.setter_edit.setValidator(QtGui.QRegExpValidator(QtCore.QRegExp('[0-9]{1,12}:[0-9]{1,12}')))
        self.listview.mouseDoubleClickEvent = get_id
        graph = main.main.graph
        for client_id in sorted(graph.client_id_dict.keys()):
            if not self.client_has_outputs(graph, client_id): continue
            client = graph.client_id_dict[client_id]
            item = QtGui.QStandardItem('{} ({})'.format(client.name, client.id))
            item.setEnabled(False)
            self.model.appendRow(item)
            for port_id in sorted(graph.port_id_dict[client_id].keys()):
                port = graph.port_id_dict[client_id][port_id]
                if not port.is_output: continue
                item = QtGui.QStandardItem(' {} ({})'.format(port.name, port.id))
                if client.type == alsaseq.SEQ_KERNEL_CLIENT:
                    setBold(item)
                item.setData('{}:{}'.format(client.id, port.id), IdRole)
                self.model.appendRow(item)
        self.setter_edit.textChanged.connect(highlight)
        if self.exec_() and self.setter_edit.text():
            return self.setter_edit.text()
        else:
            return None

    @classmethod
    def port_name_dialog(cls, main):
        def get_name(event):
            item = self.model.item(self.listview.indexAt(event.pos()).row())
            sel_name = str(item.data(NameRole).toString()).replace('(', '\(').replace(')', '\)')
            if self.setter_edit.text() == sel_name:
                self.accept()
            self.setter_edit.setText(sel_name)
        def highlight(text):
            print 'searching: {}'.format(text)
            try:
                regex = re.compile('{}'.format(str(text)))
            except:
                [setItalic(item, False) for item in [self.model.item(row) for row in range(self.model.rowCount())]]
                self.buttonBox.button(QtGui.QDialogButtonBox.Ok).setEnabled(False)
                return
            self.buttonBox.button(QtGui.QDialogButtonBox.Ok).setEnabled(True)
            for item in [self.model.item(row) for row in range(self.model.rowCount())]:
                print 'pattern: {}\tstring: {}'.format(regex.pattern, item.data(NameRole).toString())
                rm = regex.match(item.data(NameRole).toString())
                if rm: print rm.string
                setItalic(item, True if regex.match(item.data(NameRole).toString()) is not None else False)
        self = cls(main)
        self.description_lbl.setText(
                                     'Insert a client name or select it from the list of the existing clients.\n\n'+
                                     'Remember, with ALSA multiple client can share the same name, and this filter'+
                                     'will behave in the same way. On the other hand, JACK uses unique names, so'+
                                     'starting multiple instances of the same program will result in clients named'+
                                     '"client_01", "client_02", etc. In that case, you can use a regex, eg:'+
                                     '"client_name.*" will match every client name starting with "client_name"'
                                     )
        self.setter_lbl.setText('Client name')
        self.listview.mouseDoubleClickEvent = get_name
        graph = main.main.graph
        for client_id in sorted(graph.client_id_dict.keys()):
            if not self.client_has_outputs(graph, client_id): continue
            client = graph.client_id_dict[client_id]
            item = QtGui.QStandardItem('{} ({})'.format(client.name, client.id))
            item.setData('{}:.*'.format(client.name), NameRole)
            item.setEnabled(False)
            self.model.appendRow(item)
            for port_id in sorted(graph.port_id_dict[client_id].keys()):
                port = graph.port_id_dict[client_id][port_id]
                if not port.is_output: continue
                item = QtGui.QStandardItem(' {} ({})'.format(port.name, port.id))
                if client.type == alsaseq.SEQ_KERNEL_CLIENT:
                    setBold(item)
                item.setData('{}:{}'.format(client.name, port.name), NameRole)
                self.model.appendRow(item)
        self.setter_edit.textChanged.disconnect()
        self.setter_edit.textChanged.connect(highlight)
        if self.exec_() and self.setter_edit.text():
            return self.setter_edit.text()
        else:
            return None


class SettingsDialog(QtGui.QDialog):
    class FilterDelegate(QtGui.QStyledItemDelegate):
        def __init__(self, parent=None, format=None):
            QtGui.QStyledItemDelegate.__init__(self, parent)
            if format:
                format = QtCore.QRegExp(format)
                self.validator = QtGui.QRegExpValidator(format, self)
            else:
                self.validator = None

        def createEditor(self, parent, option, index):
            editor = QtGui.QStyledItemDelegate.createEditor(self, parent, option, index)
            if self.validator:
                editor.setValidator(self.validator)
            return editor

    def __init__(self, main):
        QtGui.QDialog.__init__(self, parent=None)
        _load_ui(self, 'settings.ui')
        self.main = main
        self.settings = self.main.settings
        #load filter settings will go here
        self.event_type_filter = self.settings.gFilters.get_event_type(set(), False)
        self.client_id_filter = self.settings.gFilters.get_client_id(set(), False)
        self.port_id_filter = self.settings.gFilters.get_port_id(set(), False)
        self.port_name_filter = self.settings.gFilters.get_port_name(set(), False)

        self.max_rec_spin.setValue(self.settings.gGeneral.max_rec)
        self.minimum_time_spin.setValue(self.settings.gGeneral.minimum_time)
        self.last_event_limit_spin.setValue(self.settings.gGeneral.last_event_limit)
        self.tick_res_spin.setValue(self.settings.gGeneral.tick_res)
        self.autosave_chk.setChecked(self.settings.gGeneral.autosave)
        self.autosave_path_edit.setText(self.settings.gGeneral.get_autosave_path(''))

        self.autosave_chk.toggled.connect(self.autosave_set)
        self.autosave_path_btn.clicked.connect(self.autosave_path)
        self.allnotes_chk.clicked.connect(self.allnotes_click)
        self.noteon_chk.toggled.connect(self.note_toggled)
        self.noteon_chk.toggled.connect(lambda state: self.filter_event_set(NOTEON, state))
        self.noteoff_chk.toggled.connect(self.note_toggled)
        self.noteoff_chk.toggled.connect(lambda state: self.filter_event_set(NOTEOFF, state))
        self.ctrl_chk.toggled.connect(lambda state: self.filter_event_set(CTRL, state))
        self.pitchbend_chk.toggled.connect(lambda state: self.filter_event_set(PITCHBEND, state))
        self.aftertouch_chk.toggled.connect(lambda state: self.filter_event_set(AFTERTOUCH, state))
        self.polyaftertouch_chk.toggled.connect(lambda state: self.filter_event_set(POLY_AFTERTOUCH, state))
        self.program_chk.toggled.connect(lambda state: self.filter_event_set(PROGRAM, state))
        self.sysex_chk.toggled.connect(lambda state: self.filter_event_set(SYSEX, state))

        self.build_models()

        self.buttonBox.button(QtGui.QDialogButtonBox.RestoreDefaults).clicked.connect(self.defaults)
        self.buttonBox.button(QtGui.QDialogButtonBox.Apply).clicked.connect(self.check_changes)
        self.accepted.connect(self.check_changes)


    def build_models(self):
        self.client_id_model = QtGui.QStandardItemModel()
        self.client_id_list.setModel(self.client_id_model)
        self.client_id_list.setItemDelegate(self.FilterDelegate(self, '[0-9]+'))
        self.port_id_model = QtGui.QStandardItemModel()
        self.port_id_list.setModel(self.port_id_model)
        self.port_id_list.setItemDelegate(self.FilterDelegate(self, '[0-9]+:[0-9]+'))
        self.port_name_model = QtGui.QStandardItemModel()
        self.port_name_list.setModel(self.port_name_model)
        self.port_name_list.setItemDelegate(self.FilterDelegate(self, '.+'))

        ref_data = namedtuple('ref_data', 'model list filter add_btn del_btn dialog')
        reference_list = ['client_id', 'port_id', 'port_name']
        self.reference = {}
        for ref in reference_list:
            model = getattr(self, '{}_model'.format(ref))
            list = getattr(self, '{}_list'.format(ref))
            filter = getattr(self, '{}_filter'.format(ref))
            add_btn = getattr(self, '{}_add_btn'.format(ref))
            del_btn = getattr(self, '{}_del_btn'.format(ref))
            dialog = getattr(SourceFilterDialog, '{}_dialog'.format(ref))
            self.reference[ref] = ref_data(model, list, filter, add_btn, del_btn, dialog)
            for f in filter:
                item = QtGui.QStandardItem(str(f))
                model.appendRow(item)

            add_btn.clicked.connect(lambda state, ref=ref: self.filter_dialog(ref))
            del_btn.clicked.connect(lambda state, ref=ref: self.item_delete(ref))
            model.rowsInserted.connect(lambda index, start, end, ref=ref: self.model_check(ref))
            model.rowsRemoved.connect(lambda index, start, end, ref=ref: self.model_check(ref))
            self.model_check(ref)

    def defaults(self):
        res = QtGui.QMessageBox.question(self, 'Restore defaults?', 'Restore <b>all</b> values to default?', 
                                         QtGui.QMessageBox.Ok|QtGui.QMessageBox.Cancel)
        if not res: return
        self.max_rec_spin.setValue(defaults['max_rec'])
        self.autosave_chk.setChecked(False)
        self.autosave_path_edit.setText('')
        self.minimum_time_spin.setValue(defaults['minimum_time'])
        self.last_event_limit_spin.setValue(defaults['last_event_limit'])
        self.tick_res_spin.setValue(defaults['tick_res'])
        for btn in self.event_btn_group.buttons():
            btn.setChecked(False)
        for row in range(self.client_id_model.rowCount()):
            item = self.client_id_model.takeRow(row)[0]
            del item
        for row in range(self.port_id_model.rowCount()):
            item = self.port_id_model.takeRow(row)[0]
            del item
        for row in range(self.port_name_model.rowCount()):
            item = self.port_name_model.takeRow(row)[0]
            del item

    def filter_dialog(self, ref):
        ref = self.reference[ref]
        res = ref.dialog(self)
        if res is not None:
            if ref.model.findItems(res): return
            item = QtGui.QStandardItem(str(res))
            ref.model.appendRow(item)
            ref.list.setCurrentIndex(ref.model.index(ref.model.rowCount()-1, 0))

    def item_delete(self, ref):
        row = self.reference[ref].list.currentIndex().row()
        if row < 0: return
        item = self.reference[ref].model.takeRow(row)[0]
        del item

    def model_check(self, ref):
        model = self.reference[ref].model
        self.reference[ref].del_btn.setEnabled(True if model.rowCount() else False)

    def allnotes_click(self, state):
        if self.allnotes_chk.checkState() > 0:
            self.noteon_chk.setChecked(True)
            self.noteoff_chk.setChecked(True)
        else:
            self.noteon_chk.setChecked(False)
            self.noteoff_chk.setChecked(False)

    def note_toggled(self, state):
        if all([self.noteon_chk.isChecked(), self.noteoff_chk.isChecked()]):
            self.allnotes_chk.setCheckState(QtCore.Qt.Checked)
        elif any([self.noteon_chk.isChecked(), self.noteoff_chk.isChecked()]):
            self.allnotes_chk.setCheckState(QtCore.Qt.PartiallyChecked)
        else:
            self.allnotes_chk.setChecked(False)

    def filter_event_set(self, event_type, state):
        if state:
            self.event_type_filter.add(event_type)
        else:
            self.event_type_filter.discard(event_type)

    def check_changes(self):
        settings = {
                    'gGeneral': {
                                 'minimum_time': self.minimum_time_spin.value, 
                                 'last_event_limit': self.last_event_limit_spin.value, 
                                 'tick_res': self.tick_res_spin.value, 
                                 'max_rec': self.max_rec_spin.value, 
                                 'autosave': self.autosave_chk.isChecked, 
                                 'autosave_path': lambda: str(self.autosave_path_edit.text()) if self.autosave_path_edit.text() else None, 
                                 }, 
                    'gFilters': {
                                 'event_type': lambda: self.event_type_filter if self.event_type_filter else None, 
                                 'client_id': lambda: set(
                                      map(int, [self.client_id_model.item(row).text() for row in range(self.client_id_model.rowCount())])
                                      ) if self.client_id_model.rowCount() else None, 
                                 'port_id': lambda: set(
                                      [tuple(map(int, str(self.port_id_model.item(row).text()).split(':'))) for row in range(self.port_id_model.rowCount())]
                                      ) if self.port_id_model.rowCount() else None, 
                                 'port_name': lambda: set(
                                      [str(self.port_name_model.item(row).text()) for row in range(self.port_name_model.rowCount())]
                                      ) if self.port_name_model.rowCount() else None, 
                                 }, 
                    }
        for cat_str, sets in settings.items():
            cat = getattr(self.settings, cat_str)
            for attr, getter in sets.items():
                if getattr(cat, attr) != getter():
                    getattr(cat, 'set_{}'.format(attr))(getter())


    def autosave_set(self, state):
        self.autosave_path_edit.setEnabled(state)
        self.autosave_path_btn.setEnabled(state)
        if state and not self.autosave_path_edit.text():
            res = self.autosave_path()
            if not res:
                self.autosave_chk.setChecked(False)

    def autosave_path(self):
        def check():
            path = win.selectedFiles()[0]
            if QtCore.QFileInfo(path).permissions() & QtCore.QFile.WriteOwner:
                win.accept()
                self.autosave_path_edit.setText(path)
            else:
                QtGui.QMessageBox.warning(win, 'Write error', 'The selected path has no write permissions.')
        win = QtGui.QFileDialog(self, 'Select output directory')
        win.setFileMode(QtGui.QFileDialog.Directory)
        win.setOptions(QtGui.QFileDialog.ShowDirsOnly|QtGui.QFileDialog.HideNameFilterDetails)
        win.setAcceptMode(QtGui.QFileDialog.AcceptOpen)
        buttonBox = win.findChild(QtGui.QDialogButtonBox)
        open_btn = buttonBox.button(QtGui.QDialogButtonBox.Open)
        open_btn.clicked.disconnect()
        open_btn.clicked.connect(check)
        return win.exec_()

class ExportWin(QtGui.QDialog):
    class DestDelegate(QtGui.QStyledItemDelegate):
        def __init__(self, parent=None):
            QtGui.QStyledItemDelegate.__init__(self, parent)
            self.allowed_chars = QtCore.QRegExp('[ -~]*')
            self.validator = QtGui.QRegExpValidator(self.allowed_chars, self)

        def createEditor(self, parent, option, index):
            editor = QtGui.QStyledItemDelegate.createEditor(self, parent, option, index)
            editor.setValidator(self.validator)
            return editor


    def __init__(self, parent=None):
        QtGui.QDialog.__init__(self, parent)
        _load_ui(self, 'export.ui')
        self.main = parent
        self.seq = self.main.seq
        self.src_bpm = self.main.src_bpm_spin.value()
        self.dest_bpm = self.main.dest_bpm_spin.value()
        self.file_name = ''
        self.filter = {'event_type': set(), 'port': set()}
        self.filled = False

        self.event_widgets = {
                   NOTEON: self.noteon_chk, NOTEOFF: self.noteoff_chk, 
                   CTRL: self.ctrl_chk, PITCHBEND: self.pitchbend_chk, 
                   AFTERTOUCH: self.aftertouch_chk, POLY_AFTERTOUCH: self.polyaftertouch_chk, 
                   PROGRAM: self.program_chk, SYSEX: self.sysex_chk, 
                   }

        self.src_model = QtGui.QStandardItemModel()
        self.src_listview.setModel(self.src_model)
        self.dest_model = QtGui.QStandardItemModel()
        self.dest_listview.setModel(self.dest_model)

        self.dest_listview.setItemDelegate(self.DestDelegate(self))
        self.src_listview.mouseDoubleClickEvent = self.src_dblclick
        self.dest_listview.mouseDoubleClickEvent = self.dest_dblclick
        self.dest_model.rowsRemoved.connect(self.reset_links)
        self.dest_model.dataChanged.connect(self.dest_update)
        self.dest_listview.viewportEvent = self.dest_viewportEvent
        self.dest_listview.customContextMenuRequested.connect(self.dest_menu)
        self.buttonBox.button(QtGui.QDialogButtonBox.Save).clicked.disconnect()
        self.buttonBox.button(QtGui.QDialogButtonBox.Save).clicked.connect(self.export)
        self.buttonBox.button(QtGui.QDialogButtonBox.Save).setText('Export')
        self.main.src_bpm_spin.valueChanged.connect(self.main_tempo_change)
        self.main.dest_bpm_spin.valueChanged.connect(self.main_tempo_change)
        self.main.ratio_changed.connect(self.main_tempo_change)
        self.src_bpm_spin.valueChanged.connect(lambda value: setattr(self, 'src_bpm', value))
        self.dest_bpm_spin.valueChanged.connect(lambda value: setattr(self, 'dest_bpm', value))
        self.allnotes_chk.clicked.connect(self.allnotes_click)
        self.noteon_chk.toggled.connect(self.note_toggled)
        self.noteon_chk.toggled.connect(lambda state: self.filter_set(NOTEON, state))
        self.noteoff_chk.toggled.connect(self.note_toggled)
        self.noteoff_chk.toggled.connect(lambda state: self.filter_set(NOTEOFF, state))
        self.ctrl_chk.toggled.connect(lambda state: self.filter_set(CTRL, state))
        self.pitchbend_chk.toggled.connect(lambda state: self.filter_set(PITCHBEND, state))
        self.aftertouch_chk.toggled.connect(lambda state: self.filter_set(AFTERTOUCH, state))
        self.polyaftertouch_chk.toggled.connect(lambda state: self.filter_set(POLY_AFTERTOUCH, state))
        self.program_chk.toggled.connect(lambda state: self.filter_set(PROGRAM, state))
        self.sysex_chk.toggled.connect(lambda state: self.filter_set(SYSEX, state))
        self.main.filterChanged.connect(self.filter_update)
        self.main.filterReset.connect(self.filter_reset)
        self.close_chk = QtGui.QCheckBox('Close this window after export')
        self.close_chk.setChecked(True)
        self.buttonBox.layout().insertWidget(1, self.close_chk)
        for chk in self.event_widgets.values():
            chk.toggled.connect(self.export_check)

    def filter_update(self, col, data, state):
        if not self.filled: return
        print col, data, state
        if col == COL_SRC_ADDR:
            for addr in data:
                port_item = self.port_dict[tuple(map(int, addr.split(':')))]
                port_item.data(DestRole).toPyObject().setData(state, EnabledRole)
        elif col == COL_SRC_CLIENT:
            for name in data:
                for item in self.src_model.findItems('{}\ \([0-9]+\)'.format(re.escape(name)), flags=QtCore.Qt.MatchRegExp):
                    if item.is_client and item.name == name:
                        for port_item in item.ports.values():
                            port_item.data(DestRole).toPyObject().setData(state, EnabledRole)
        elif col == COL_SRC_PORT:
            for name in data:
                for item in self.src_model.findItems(' {}\ \([0-9]+\)'.format(re.escape(name)), flags=QtCore.Qt.MatchRegExp):
                    if not item.is_client and item.name == name:
                        item.data(DestRole).toPyObject().setData(state, EnabledRole)
        elif col == COL_EVENT_TYPE:
            for name in data:
                self.event_widgets[globals()[name]].setChecked(not state)

    def filter_reset(self):
        for port_item in [self.dest_model.item(i) for i in range(self.dest_model.rowCount())]:
            port_item.setData(True, EnabledRole)
        for chk in self.event_widgets.values():
            chk.setChecked(False)

    def filter_set(self, event_type, state):
        if state:
            self.filter['event_type'].add(event_type)
        else:
            self.filter['event_type'].discard(event_type)

    def allnotes_click(self, state):
        if self.allnotes_chk.checkState() > 0:
            self.noteon_chk.setChecked(True)
            self.noteoff_chk.setChecked(True)
        else:
            self.noteon_chk.setChecked(False)
            self.noteoff_chk.setChecked(False)

    def note_toggled(self, state):
        if all([self.noteon_chk.isChecked(), self.noteoff_chk.isChecked()]):
            self.allnotes_chk.setCheckState(QtCore.Qt.Checked)
        elif any([self.noteon_chk.isChecked(), self.noteoff_chk.isChecked()]):
            self.allnotes_chk.setCheckState(QtCore.Qt.PartiallyChecked)
        else:
            self.allnotes_chk.setChecked(False)

    def main_tempo_change(self, value=None):
        sender = self.sender()
        if value is None:
            self.dest_bpm_spin.setValue(self.main.dest_bpm_spin.value())
        elif sender == self.main.src_bpm_spin:
            self.src_bpm_spin.setValue(value)
        elif sender == self.main.dest_bpm_spin:
            self.dest_bpm_spin.setValue(value)

    def src_dblclick(self, event):
        item = self.src_listview.model().item(self.src_listview.indexAt(event.pos()).row())
        if item.is_client:
            export = all([port_item.export for port_item in item.ports.values()])
            for port_item in item.ports.values():
                port_item.data(DestRole).toPyObject().setData(not export, EnabledRole)
                
        else:
            dest_item = item.data(DestRole).toPyObject()
            dest_item.setData(not dest_item.data(EnabledRole).toBool(), EnabledRole)

    def dest_dblclick(self, event):
        item = self.dest_model.item(self.dest_listview.indexAt(event.pos()).row())
        item.setData(not item.data(EnabledRole).toBool(), EnabledRole)

    def dest_update(self, first_index, last_index):
        item = self.dest_model.item(first_index.row())
        export = item.data(EnabledRole).toBool()
        item.setEnabled(export)
        port_item = self.port_dict[item.data(DestRole).toPyObject()]
        setBold(port_item, export)
        port_item.export = export
        if any([self.dest_model.item(i).data(EnabledRole).toBool() for i in range(self.dest_model.rowCount())]):
            self.buttonBox.button(QtGui.QDialogButtonBox.Save).setEnabled(True if self.export_check() else False)
        else:
            self.buttonBox.button(QtGui.QDialogButtonBox.Save).setEnabled(False)

    def dest_viewportEvent(self, event):
        if event.type() == QtCore.QEvent.ToolTip:
            item = self.dest_model.itemFromIndex(self.dest_listview.indexAt(event.pos()))
            if item:
                self.dest_listview.setToolTip(item.name)
            else:
                self.dest_listview.setToolTip('')
        return QtGui.QListView.viewportEvent(self.dest_listview, event)

    def dest_menu(self, pos):
        def reorder():
            order = {}
            for i in range(self.dest_model.rowCount()-1, -1, -1):
                item = self.dest_model.takeRow(i)[0]
                order[item.data(OrdRole).toInt()[0]] = item
            for i in sorted(order):
                self.dest_model.appendRow(order[i])
            
        index = self.dest_listview.indexAt(pos)
        item = self.dest_model.itemFromIndex(index)
        menu = QtGui.QMenu()
        if item:
            enable = QtGui.QAction('Export', menu)
            enable.setCheckable(True)
            enable.setChecked(True if item.isEnabled() else False)
            enable.triggered.connect(lambda state, i=item: i.setData(state, EnabledRole))
            menu.addAction(enable)
            if item.isEnabled():
                rename = QtGui.QAction('Rename', menu)
                rename.triggered.connect(lambda _, i=index: self.dest_listview.edit(i))
                menu.addAction(rename)
                orig_name = str(item.data(NameRole).toString())
                if str(item.text()) != orig_name:
                    reset = QtGui.QAction('Restore original name', menu)
                    reset.triggered.connect(lambda _, i=item, n=orig_name: i.setText(n))
                    menu.addAction(reset)
            sep = QtGui.QAction(menu)
            sep.setSeparator(True)
            menu.addAction(sep)
        sort = QtGui.QAction('Restore original order', menu)
        sort.triggered.connect(reorder)
        menu.addAction(sort)
        menu.exec_(self.sender().mapToGlobal(pos))


    def exec_(self):
        disabled = len([event for event in self.main.event_buffer if not event.enabled])
        txt = str(self.disabled_chk.text()).split()
        for i, w in enumerate(txt):
            if w.isdigit():
                break
        txt[i] = str(disabled)
        self.disabled_chk.setText(' '.join(txt))
        self.disabled_chk.setEnabled(True if disabled else False)
        
        self.src_bpm_spin.setValue(self.src_bpm)
        self.dest_bpm_spin.setValue(self.dest_bpm)
        if not self.filled:
            self.populate()

        self.show()

    def export_check(self, *args):
        if any([all([chk.isEnabled(), not chk.isChecked()]) for chk in self.event_widgets.values()]):
            self.buttonBox.button(QtGui.QDialogButtonBox.Save).setEnabled(True)
            return True
        else:
            self.buttonBox.button(QtGui.QDialogButtonBox.Save).setEnabled(False)
            return False

    def export(self):
        res = QtGui.QFileDialog.getSaveFileName(
                                                self, 
                                                'Export to MIDI file', 
                                                self.file_name, 
                                                ('MIDI file (*.mid)(*.mid);;Show all files (*)'), 
                                                )
        if not res:
            return

        self.file_name = str(res)

        tick_res = defaults['tick_res']
        pattern = midi.Pattern(resolution=tick_res)
        track_dict = {}
        for line in range(self.dest_model.rowCount()):
            if not self.dest_model.item(line).data(EnabledRole).toBool(): continue
            item = self.dest_model.item(line)
            track = midi.Track(tick_relative=False)
            pattern.append(track)
            track_dict[item.data(DestRole).toPyObject()] = track
            track.append(midi.TrackNameEvent(data=[b for b in bytearray(str(item.text()))]))
#        pattern[0].append(midi.SetTempoEvent(bpm=self.dest_bpm_spin.value()))
        source_bpm = self.src_bpm_spin.value()

        for event, time, source, enabled in self.event_buffer:
            if not enabled and not self.disabled_chk.isChecked: continue
            _, _, dest = source
            if dest not in track_dict.keys(): continue
            #event check for "fake" NOTEON with velocity=0, which are considered as NOTEOFF
            event_type = event.type
            if event_type == NOTEON and event.data2 == 0:
                event_type = NOTEOFF
            if event_type in self.filter['event_type']: continue

            event_class, dest_attr = ev_dict[event.type]
            if isinstance(dest_attr, list):
                data = [getattr(event, attr) for attr in dest_attr]
            else:
                data = dest_attr(event)
                print 'sysex! sysex={}, data={}'.format(event.sysex, data)
            ticks = int((time*10**-9)/60.*source_bpm*tick_res)
            output_event = event_class(tick=ticks, channel=event.channel, data=data)
            track_dict[dest].append(output_event)
        for track in pattern:
            last_tick = track[-1].tick
            track.append(midi.EndOfTrackEvent(tick=last_tick))

        pattern.make_ticks_rel()
        print pattern
        for event, time, source, enabled in self.event_buffer:
            if event.type == SYSEX:
                print event.sysex
        midi.write_midifile(self.file_name, pattern)
        if self.close_chk.isChecked():
            self.accept()

    def populate(self):
        self.event_buffer = self.main.event_buffer
        self.port_dict = {}
        client_dict = {}
        ev_set = set()
        for event, time, source, enabled in self.event_buffer:
            if event.type == NOTEON and event.data2 == 0:
                ev_set.add(NOTEOFF)
            else:
                ev_set.add(event.type)
            client_name, port_name, (client_id, port_id) = source
            if not client_id in client_dict:
                client_dict[client_id] = {
                                          'name': client_name, 
                                          'ports': {port_id: port_name}, 
                                          }
            else:
                client_dict[client_id]['ports'][port_id] = port_name
        for t, widget in self.event_widgets.items():
            widget.setEnabled(True if t in ev_set else False)
        self.allnotes_chk.setEnabled(True if set([NOTEON, NOTEOFF]).issubset(ev_set) else False)
        self.track_items = {}
        dest_list_index = 0
        for client_id in sorted(client_dict.keys()):
            client_name = client_dict[client_id]['name']
            client_item = QtGui.QStandardItem('{} ({})'.format(client_name, client_id))
            client_item.setForeground(QtGui.QBrush(QtCore.Qt.gray))
            client_item.export = True if not client_name in self.main.filter_list[COL_SRC_CLIENT] else False
            client_item.name = client_name
            client_item.is_client = True
            client_item.ports = {}
            self.src_model.appendRow(client_item)
            for port_id, port_name in [(p, client_dict[client_id]['ports'][p]) for p in sorted(client_dict[client_id]['ports'].keys())]:
                port_item = QtGui.QStandardItem(' {} ({})'.format(port_name, port_id))
                addr = '{}:{}'.format(client_id, port_id)
                if client_item.export and not (addr in self.main.filter_list[COL_SRC_ADDR] or port_name in self.main.filter_list[COL_SRC_PORT]):
                    export = True
                else:
                    export = False
                port_item.export = export
                port_item.name = port_name
                port_item.is_client = False
                client_item.ports[port_id] = port_item
                setBold(port_item, export)
                self.src_model.appendRow(port_item)
                self.port_dict[(client_id, port_id)] = port_item

                name = '{} ({})'.format(port_name, port_id)
                dest_item = QtGui.QStandardItem(name)
                dest_item.setFlags(dest_item.flags() ^ QtCore.Qt.ItemIsDropEnabled)
                dest_item.setData(name, NameRole)
                dest_item.setData(dest_list_index, OrdRole)
                dest_item.setData((client_id, port_id), DestRole)
                dest_item.setData(export, EnabledRole)
                dest_item.setEnabled(export)
                dest_list_index += 1
                self.dest_model.appendRow(dest_item)
                self.track_items[(client_id, port_id)] = dest_item
                port_item.setData(dest_item, DestRole)
        self.filled = True

    def reset_links(self):
        for dest_item in [self.dest_model.item(i) for i in range(self.dest_model.rowCount())]:
            if dest_item is None: continue
            source_item = self.port_dict[dest_item.data(DestRole).toPyObject()]
            source_item.setData(dest_item, DestRole)


class MidiInspector(QtGui.QMainWindow):
    ratio_changed = QtCore.pyqtSignal()
    filterChanged = QtCore.pyqtSignal(int, object, bool)
    filterReset = QtCore.pyqtSignal()
    header_columns = ['Time', 'Time (BBT)', 'Src Addr', 'Src Client', 'Src Port', 'Event Type', 'Ch', 'Param', 'Value', 'Ext']
    txt_str = {
               COL_SRC_ADDR: 'from {}', 
               COL_SRC_CLIENT: 'from client "{}"', 
               COL_SRC_PORT: 'from port "{}"', 
               COL_CHAN: 'from channel {}', 
               COL_DATA1: 'with parameter {}', 
               COL_DATA2: 'with value {}', 
               COL_EXT: 'with this format', 
               }
    def __init__(self, main, event_buffer, play):
        QtGui.QMainWindow.__init__(self, parent=None)
        _load_ui(self, 'inspector.ui')
        self.main = main
        self.seq = self.main.seq
        self.output_event = self.single_output_event
        self.event_buffer = event_buffer
        self.event_buffer.quantize_start()
        self.start_time = 0
        self.start_item = 0
        self.pause_time = 0
        self.column_data = [None, None, set(), [], [], set(), set(), set(), set(), set()]
        self.filter_list = [set() for i in self.column_data]
        self.horizontalHeader = self.event_table.horizontalHeader()
        self.horizontalHeader.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.horizontalHeader.customContextMenuRequested.connect(self.top_header_menu)
        self.verticalHeader = self.event_table.verticalHeader()
        self.verticalHeader.setResizeMode(QtGui.QHeaderView.Fixed)
        self.verticalHeader.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.verticalHeader.customContextMenuRequested.connect(self.side_header_menu)
        self.event_table.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.event_table.customContextMenuRequested.connect(self.item_menu)
        self.event_table.doubleClicked.connect(lambda index: self.set_start(index.row()))
        self.actionRestart.triggered.connect(self.restart)
        self.actionPlay.triggered.connect(self.play)
        self.actionStop.triggered.connect(self.stop)
        self.actionPause.triggered.connect(self.pause)
#        for action in self.toolBar.actions():
#            action.setIconText(action.text())
        QtGui.QIcon.setThemeName(QtGui.QApplication.style().objectName())
        self.actionExport.setIcon(QtGui.QIcon.fromTheme('document-save'))

        self.do_timers()
        self.do_ev_filters()
        self.export_win = ExportWin(self)
        self.actionExport.triggered.connect(self.export_win.exec_)


        self.model = QtGui.QStandardItemModel()
        self.model.setHorizontalHeaderLabels(self.header_columns)
        self.model.headerData = self.headerData
        self.event_table.setModel(self.model)
        client_order = {}
        port_order = {}
        event_index = 1
        for i, data in enumerate(self.event_buffer.data):
            event, time, source, enabled = data
            if isinstance(event, MidiEvent):
                self.play_timer.started.connect(data.timer_start)
                data.play.connect(lambda ev=event: self.output_event(ev))
                data.play.connect(lambda i=i: self.event_table.selectRow(i))
                str_time = QtGui.QStandardItem(self.repr_time(time))
    #            str_time.time = time
                bbt = QtGui.QStandardItem(self.time_to_bbt(time))
                bbt.setTextAlignment(QtCore.Qt.AlignRight)
                if source:
                    if source.addr:
                        addr = '{}:{}'.format(*source.addr)
                        source_id = QtGui.QStandardItem(addr)
                        source_id.setTextAlignment(QtCore.Qt.AlignHCenter)
                        self.column_data[COL_SRC_ADDR].add(addr)
                    else:
                        source_id = QtGui.QStandardItem()
                    client = QtGui.QStandardItem(source.client)
                    port = QtGui.QStandardItem(source.port)
                    if not source.client in self.column_data[COL_SRC_CLIENT]:
                        self.column_data[COL_SRC_CLIENT].append(source.client)
                        if source.addr:
                            client_order[source.client] = source.addr[0]
                    if not source.port in self.column_data[COL_SRC_PORT]:
                        self.column_data[COL_SRC_PORT].append(source.port)
                        if source.addr:
                            port_order[source.port] = source.addr
                else:
                    source_id = QtGui.QStandardItem()
                    client = QtGui.QStandardItem()
                    port = QtGui.QStandardItem()
                event_type_str = str(event.type)
                if event.type == NOTEON:
                    if event.data2 > 0:
                        event_type_str = 'NOTEON'
                    else:
                        event_type_str = 'NOTEOFF'
                event_type = QtGui.QStandardItem(event_type_str)
                self.column_data[COL_EVENT_TYPE].add(event_type_str)
                channel = QtGui.QStandardItem(str(event.channel))
                self.column_data[COL_CHAN].add(str(event.channel))
                param = QtGui.QStandardItem(str(event.data1))
                self.column_data[COL_DATA1].add(str(event.data1))
                value = QtGui.QStandardItem(str(event.data2))
                self.column_data[COL_DATA2].add(str(event.data2))
                if event.sysex:
                    ext_str = ' '.join('{:02X}'.format(byte) for byte in event.sysex)
                    ext = QtGui.QStandardItem(ext_str)
                    self.column_data[COL_EXT].add(ext_str)
                else:
                    ext = QtGui.QStandardItem('')
                self.model.appendRow([str_time, bbt, source_id, client, port, event_type, channel, param, value, ext])
                self.model.setHeaderData(i, QtCore.Qt.Vertical, QtCore.QVariant(event_index), QtCore.Qt.DisplayRole)
                event_index += 1
            else:
                print 'connection event: {}'.format(event)
                self.play_timer.started.connect(data.timer_start)
                data.play.connect(lambda i=i: self.event_table.selectRow(i))
                str_time = QtGui.QStandardItem(self.repr_time(time))
                bbt = QtGui.QStandardItem(self.time_to_bbt(time))
                bbt.setTextAlignment(QtCore.Qt.AlignRight)
                if source:
                    if source.addr:
                        addr = '{}:{}'.format(*source.addr)
                        source_id = QtGui.QStandardItem(addr)
                        source_id.setTextAlignment(QtCore.Qt.AlignHCenter)
                        self.column_data[COL_SRC_ADDR].add(addr)
                    else:
                        source_id = QtGui.QStandardItem()
                    client = QtGui.QStandardItem(source.client)
                    port = QtGui.QStandardItem(source.port)
                    if not source.client in self.column_data[COL_SRC_CLIENT]:
                        self.column_data[COL_SRC_CLIENT].append(source.client)
                        if source.addr:
                            client_order[source.client] = source.addr[0]
                    if not source.port in self.column_data[COL_SRC_PORT]:
                        self.column_data[COL_SRC_PORT].append(source.port)
                        if source.addr:
                            port_order[source.port] = source.addr
                else:
                    source_id = QtGui.QStandardItem()
                    client = QtGui.QStandardItem()
                    port = QtGui.QStandardItem()
                event_type = QtGui.QStandardItem('{}connect'.format('' if event.state else 'dis'))
                channel = QtGui.QStandardItem('')
                param = QtGui.QStandardItem(str(event.dest.addr[0]))
                value = QtGui.QStandardItem(str(event.dest.addr[1]))
                ext = QtGui.QStandardItem('{}:{}'.format(event.dest.client, event.dest.port))
                self.model.appendRow([str_time, bbt, source_id, client, port, event_type, channel, param, value, ext])
                for item in [str_time, bbt, source_id, client, port, event_type, channel, param, value, ext]:
                    item.setEnabled(False)
                self.model.setHeaderData(i, QtCore.Qt.Vertical, QtCore.QVariant('*'), QtCore.Qt.DisplayRole)

        #header filter items reordering
        self.column_data[COL_SRC_ADDR] = sorted(self.column_data[COL_SRC_ADDR], key=lambda s: map(int, s.split(':')))
        self.column_data[COL_SRC_CLIENT] = sorted(self.column_data[COL_SRC_CLIENT], key=client_order.__getitem__)
        self.column_data[COL_SRC_PORT] = sorted(self.column_data[COL_SRC_PORT], key=port_order.__getitem__)
        self.column_data[COL_CHAN] = sorted(self.column_data[COL_CHAN])
        self.column_data[COL_DATA1] = sorted(self.column_data[COL_DATA1])
        self.column_data[COL_DATA2] = sorted(self.column_data[COL_DATA2])

        lbl = QtGui.QLabel('<b>START</b>: {sd} at {st}'.format(
                                             sd = self.event_buffer.start.toString('ddd dd/M/yyyy'), 
                                             st = self.event_buffer.start.toString('HH:mm:ss'), 
                                             ))
        self.statusbar.addWidget(lbl)
        space = QtGui.QWidget()
        space.setMinimumWidth(10)
        self.statusbar.addWidget(space)
        lbl = QtGui.QLabel('<b>END</b>: {ed} at {et}'.format(
                                             ed = self.event_buffer.end.toString('ddd dd/M/yyyy'), 
                                             et = self.event_buffer.end.toString('HH:mm:ss'), 
                                             ))
        self.statusbar.addWidget(lbl)
        self.statusbar.addWidget(QtGui.QWidget(), 2)
        notes = self.event_buffer.note_count
        length = self.event_buffer.slength
        secs = length
        mins = length//60
        if mins: secs = length%60
        event_txt = QtGui.QLabel('{e} events{n} in {m}{s}'.format(
                                                            e = len(self.event_buffer),
                                                            n = ' ({} note{})'.format(notes, 's' if notes > 1 else '') if notes else '', 
                                                            m = '{}\''.format(mins) if mins else '', 
                                                            s = '{:02d}"'.format(secs) if mins else '{}"'.format(secs), 
                                                            ))
        self.statusbar.addWidget(event_txt)

        self.show()
        self.verticalHeader.setMinimumWidth(self.verticalHeader.width())

        self.event_table.resizeColumnsToContents()
        self.activateWindow()
        if play:
            self.play()


    @property
    def ratio(self):
        return self.dest_bpm_spin.value()/self.src_bpm_spin.value()

    def headerData(self, section, orientation, role):
#        if orientation == QtCore.Qt.Vertical and role == QtCore.Qt.DisplayRole:
#            print section
#            return QtCore.QVariant('p')
        if orientation == QtCore.Qt.Vertical and role == QtCore.Qt.FontRole:
            font = QtGui.QFont(self.model.headerData(0, QtCore.Qt.Horizontal, role))
            if section == self.start_item:
                font.setBold(True)
            else:
                font.setBold(False)
            return font
        return QtGui.QStandardItemModel.headerData(self.model, section, orientation, role)

    def do_ev_filters(self):
        for actionBtn in self.toolBar.findChildren(QtGui.QWidget):
            if isinstance(actionBtn, QtGui.QToolButton) and actionBtn.defaultAction() == self.actionStop:
                actionBtn.installEventFilter(self)
                self.actionStop_btn = actionBtn
        self.src_bpm_spin.lineEdit().installEventFilter(self)
        self.dest_bpm_spin.lineEdit().installEventFilter(self)


    def eventFilter(self, source, event):
        if source not in [self.actionStop_btn, self.src_bpm_spin.lineEdit(), self.dest_bpm_spin.lineEdit()]:
            return QtGui.QMainWindow.eventFilter(self, source, event)
        if source == self.actionStop_btn:
            if event.type() not in [QtCore.QEvent.MouseButtonDblClick, QtCore.QEvent.ContextMenu]:
                return QtGui.QMainWindow.eventFilter(self, source, event)
            if event.type() == QtCore.QEvent.ContextMenu:
                return True
            if event.type() == QtCore.QEvent.MouseButtonDblClick:
                self.restart()
                return True
        elif source == self.src_bpm_spin.lineEdit() and event.type() == QtCore.QEvent.MouseButtonRelease and event.button() == 4:
            self.src_bpm_spin.setValue(120)
            return True
        elif source == self.dest_bpm_spin.lineEdit() and event.type() == QtCore.QEvent.MouseButtonRelease and event.button() == 4:
            self.dest_bpm_spin.setValue(self.src_bpm_spin.value())
            return True
        return QtGui.QMainWindow.eventFilter(self, source, event)

    def activate(self, play=False):
        self.activateWindow()
        if play:
            self.play()

    def toolbar_spacer(self):
        spacer = QtGui.QWidget()
        spacer.setMinimumWidth(8)
        spacer.setMaximumWidth(8)
        return spacer

    def do_timers(self):
        self.play_timer = PlayerTimer()
        self.watch_timer = QtCore.QTimer()
        self.watch_timer.setInterval(50)
        self.watch_timer.timeout.connect(self.watch_update)
        self.stop_timer = QtCore.QTimer()
        self.stop_timer.setSingleShot(True)
        self.stop_timer.timeout.connect(self.watch_timer.stop)

        self.addToolBarBreak()
        self.time_toolBar = QtGui.QToolBar(self)
#        self.time_toolBar.setMovable(False)
        self.time_toolBar.setAllowedAreas(QtCore.Qt.TopToolBarArea)
        self.time_toolBar.setIconSize(self.toolBar.iconSize())
        self.time_toolBar.setFloatable(False)
        self.addToolBar(self.time_toolBar)

        self.timer_lbl = QtGui.QLabel('00:00.0')
#        font = self.timer_lbl.font()
        font = QtGui.QFont('Monospace')
        font.setStyleHint(QtGui.QFont.Monospace)
        metrics = QtGui.QFontMetrics(font)
        max_width = 0
        for n in range(10):
            if metrics.width(str(n)) > max_width:
                max_width = metrics.width(str(n))
                max_char = n
        max_num_size = metrics.width('{c}{c}{c}:{c}{c}.{c}'.format(c=max_char))
        self.timer_lbl.setMaximumWidth(max_num_size)
        self.timer_lbl.setMinimumWidth(max_num_size)
        self.timer_lbl.setFont(font)
        self.timer_lbl.setAlignment(QtCore.Qt.AlignRight|QtCore.Qt.AlignVCenter)
        self.time_toolBar.addWidget(self.timer_lbl)
        self.time_toolBar.addSeparator()

        meter_lbl = QtGui.QLabel('Meter:')
        self.time_toolBar.addWidget(meter_lbl)
        self.meter = 4, defaults['tick_res']
        self.meter_combo = QtGui.QComboBox()
        self.meter_model = QtGui.QStandardItemModel()
        self.meter_combo.setModel(self.meter_model)
        meters = (
                  '4/4', 
                  '3/4', 
                  '2/4', 
                  '12/8', 
                  '6/8', 
                  '3/8', 
                  )
        for meter in meters:
            item = QtGui.QStandardItem(meter)
            self.meter_model.appendRow(item)
        self.meter_combo.setEditable(True)
        self.meter_combo.setInsertPolicy(QtGui.QComboBox.InsertAtTop)
        self.meter_combo.currentIndexChanged['QString'].connect(self.meter_change)
        self.time_toolBar.addWidget(self.meter_combo)
        self.time_toolBar.addWidget(self.toolbar_spacer())

        src_lbl = QtGui.QLabel('Source BPM:')
        self.time_toolBar.addWidget(src_lbl)
        self.src_bpm_spin = QtGui.QDoubleSpinBox()
        self.src_bpm_spin.setMinimum(10)
        self.src_bpm_spin.setMaximum(350)
        self.src_bpm_spin.setValue(1)
        self.time_toolBar.addWidget(self.src_bpm_spin)
        self.time_toolBar.addWidget(self.toolbar_spacer())
        dest_lbl = QtGui.QLabel('Dest BPM:')
        self.time_toolBar.addWidget(dest_lbl)
        self.dest_bpm_spin = QtGui.QDoubleSpinBox()
        self.dest_bpm_spin.setMinimum(10)
        self.dest_bpm_spin.setMaximum(350)
        self.dest_bpm_spin.setValue(1)
        self.time_toolBar.addWidget(self.dest_bpm_spin)
        self.ratio_spin = QtGui.QDoubleSpinBox()
        self.ratio_spin.setMaximum(1750)
        self.ratio_spin.setPrefix('x')
        self.ratio_spin.setSingleStep(0.05)
        self.ratio_spin.setMinimum(0.05)
        self.ratio_spin.setKeyboardTracking(False)
        self.ratio_spin.focusOutEvent = self.ratio_spin_focus_out
        self.ratio_spin.focusInEvent = self.ratio_spin_focus_in
        self.ratio_spin.valueFromText = self.ratio_text_input
        self.ratio_spin.valueChanged.connect(self.ratio_spin_value_changed)
        self.time_toolBar.addWidget(self.toolbar_spacer())
        ratio_lbl = QtGui.QLabel('Speed:')
        self.time_toolBar.addWidget(ratio_lbl)
        self.time_toolBar.addWidget(self.ratio_spin)

        self.src_bpm_spin.valueChanged.connect(self.set_ratio)
        self.src_bpm_spin.valueChanged.connect(self.refresh_bbt)
        self.src_bpm_spin.setValue(120)
        self.dest_bpm_spin.valueChanged.connect(self.set_ratio)
        self.dest_bpm_spin.setValue(120)

    def meter_change(self, meter):
        index = self.meter_combo.findText(meter)
        meter = str(meter.toLatin1())
        m = re.search(r'^([0-9]+)[/](\b(?:1|2|4|8|16))$', meter)
        if m is None:
            self.meter_combo.removeItem(index)
            return
        num, den = map(float, m.groups())
        self.meter = num, tick_res*4/den
        self.refresh_bbt(None)

    def time_to_bbt(self, time):
        ticks = (time*10**-9)/60.*self.src_bpm_spin.value()*defaults['tick_res']
        beats_per_measure, subdivisions = self.meter
        beats = ticks // subdivisions
        tick_rest = int(ticks % subdivisions)
        bars = int(beats // beats_per_measure)
        beat_rest = int(beats % beats_per_measure)
        return '{}|{:02d}|{:03d}'.format(bars, beat_rest, tick_rest)

    def refresh_bbt(self, value):
        if not self.event_table.model(): return
        for row, data in enumerate(self.event_buffer):
            self.event_table.model().item(row, COL_BBT).setText(self.time_to_bbt(data.time))

    def set_ratio(self, value):
        self.ratio_spin.setDecimals(2)
        self.ratio_spin.blockSignals(True)
        self.ratio_spin.setValue(self.ratio)
        self.ratio_spin_value_changed(self.ratio)
        self.ratio_spin.blockSignals(False)

    def ratio_spin_focus_in(self, event):
        self.ratio_spin.setDecimals(2)
#        self.ratio_spin.lineEdit().setText(self.ratio_spin.lineEdit().text().replace('x', ''))
#        self.ratio_spin.lineEdit().setText(self.ratio_spin.textFromValue(self.ratio_spin.value()).replace(self.ratio_spin.prefix(), ''))
        self.ratio_spin.setPrefix('')
        QtGui.QDoubleSpinBox.focusInEvent(self.ratio_spin, event)
        self.ratio_spin.lineEdit().selectAll()

    def ratio_spin_focus_out(self, event):
        value = self.ratio_spin.lineEdit().text()
        dec_locale = QtCore.QString(QtCore.QLocale().decimalPoint())
        dec_point = QtCore.QString('.')
        value = str(value.replace(dec_locale, dec_point))
        dec = len(value.rstrip('0')[value.index('.')+1:])
        self.ratio_spin.setDecimals(dec)
        self.ratio_spin.setValue(float(value))
        self.ratio_spin.setPrefix('x')
        QtGui.QDoubleSpinBox.focusOutEvent(self.ratio_spin, event)

    def ratio_text_input(self, value):
        self.ratio_spin.setDecimals(2)
        return float(str(value).replace(',', '.').replace('x', ''))

    def ratio_spin_value_changed(self, value):
        if self.sender() == self.ratio_spin:
            self.dest_bpm_spin.blockSignals(True)
            self.dest_bpm_spin.setValue(self.src_bpm_spin.value()*value)
            self.dest_bpm_spin.blockSignals(False)
        self.ratio_changed.emit()
        if self.ratio_spin.hasFocus():
            return
        value = str(value)
        dec = len(value.rstrip('0')[value.index('.')+1:])
        self.ratio_spin.setDecimals(dec if dec <= 2 else 2)

    def watch_update(self, t=None):
        if t is None:
            t = self.play_timer.elapsed_delta()
        secs = round(t/1000.%60, 1)
        mins = int(t//60000)
        self.timer_lbl.setText('{:02d}:{:04.1f}'.format(mins, secs))

    def single_output_event(self, event):
        event = event.get_event()
        event.source = self.main.alsa.output.addr
        event.dest = 0xfe, 0xfd
        print 'sending event {} (src: {}, dest: {})'.format(event, event.source, event.dest)
        self.seq.output_event(event)
        self.seq.drain_output()

    def play(self, state=False, unpause=False):
        if self.actionPause.isChecked():
            self.actionPause.setChecked(False)
            time = self.start_time+self.pause_time
        elif unpause:
            time = self.start_time+self.pause_time
        else:
            self.notes_off()
            time = self.start_time
            self.pause_time = 0
        for data in self.event_buffer:
            data.set_timer(time, self.ratio)
        self.stop_timer.setInterval(data.timer.interval())
        self.play_timer.start(time)
        self.watch_timer.start()
        self.stop_timer.start()

    def pause(self, state):
        print state
        if state:
            if not self.stop_timer.isActive():
                self.actionPause.setChecked(False)
                return
            self.pause_time = self.play_timer.elapsed()+self.pause_time
            for data in self.event_buffer:
                data.timer.stop()
            self.stop_timer.stop()
            self.watch_timer.stop()
        else:
            self.play(unpause=True)

    def stop(self):
        for data in self.event_buffer:
            data.timer.stop()
        self.stop_timer.stop()
        self.watch_timer.stop()
        self.actionPause.setChecked(False)
        self.pause_time = 0
        self.notes_off()

    def notes_off(self):
        for ch in self.column_data[COL_CHAN]:
            self.output_event(CtrlEvent(0, int(ch), 123, 0))

    def restart(self):
        self.event_table.selectRow(0)
        self.watch_update(0)
        self.set_start(0, self.stop_timer.isActive())

    def set_start(self, row=0, play=False):
        time = self.event_buffer[row].time//(10**6)/self.ratio
        self.start_time = time
        self.watch_update(time)
        old_start_item = self.start_item
        self.start_item = row
        self.event_table.selectRow(old_start_item)
        self.event_table.selectRow(self.start_item)
        self.pause_time = 0
        if play:
            self.play()

    def item_highlight(self, index):
        self.event_table.selectRow(index.row())
        self.watch_update(self.event_buffer[index.row()].time//(10**6))

    def adv_filter(self, state, source_col=None, source=None, filter_type=None, filter_param=None):
        print 'FILTER! {} {} {} {}'.format(source_col, source, filter_type, filter_param)
        event_list = []
        for row in range(self.model.rowCount()):
            event = self.event_buffer[row]
            filter_source = False
            filter_event = False
            if source_col:
                if (source_col == COL_SRC_ADDR and event.source.addr == source) or \
                    (source_col == COL_SRC_CLIENT and event.source.client == source) or \
                    (source_col == COL_SRC_PORT and event.source.port == source):
                        filter_source = True
            if filter_type is not None:
                event_type = event.event.type
                if event_type == NOTEON and event.event.data2 == 0:
                    event_type = NOTEOFF
                if event_type == filter_type:
                    filter_event = True
            if filter_param is not None and filter_event and event.event.data1 != filter_param:
                filter_event = False
            if source_col and filter_source and filter_event:
                event_list.append(row)
            elif source_col and filter_source and not filter_type:
                event_list.append(row)
            elif not source_col and filter_event:
                event_list.append(row)
        print event_list
        self.event_enable(event_list, state)

    def event_enable(self, row_list, state):
        color = QtCore.QVariant(QtGui.QBrush(QtCore.Qt.black if state else QtCore.Qt.gray))
        for row in row_list:
            data = self.event_buffer[row]
            if isinstance(data.event, ConnectionEvent): continue
            data.enabled = state
            for c in range(self.model.columnCount()):
                self.model.item(row, c).setData(color, QtCore.Qt.ForegroundRole)
        

    def item_menu(self, pos):
        menu = QtGui.QMenu()
        item = self.model.itemFromIndex(self.event_table.indexAt(pos))
        selection = set([index.row() for index in self.event_table.selectedIndexes()])
        if selection:
            if len(selection) == 1:
                event = self.event_buffer[self.event_table.selectedIndexes()[0].row()]
                event_type = event.event.type
                if event_type == NOTEON and event.event.data2 == 0:
                    event_type = NOTEOFF
                newstate = not event.enabled
                addr = event.source.addr
                addr_str = ':'.join(map(str, addr))
                client = event.source.client
                port = event.source.port
                enable_str = 'Disable' if event.enabled else 'Enable'
                adv_source_menu = QtGui.QMenu('{} by source'.format(enable_str))
                adv_toggle_addr = QtGui.QAction('{} all events from {}'.format(enable_str, addr_str), self)
                adv_toggle_addr.triggered.connect(lambda _, state=newstate, addr=addr: self.adv_filter(state, COL_SRC_ADDR, addr))
                adv_toggle_client = QtGui.QAction('{} all events from client "{}"'.format(enable_str, client), self)
                adv_toggle_client.triggered.connect(lambda _, state=newstate, client=client: self.adv_filter(state, COL_SRC_CLIENT, client))
                adv_toggle_port = QtGui.QAction('{} all events from port "{}"'.format(enable_str, port), self)
                adv_toggle_port.triggered.connect(lambda _, state=newstate, port=port: self.adv_filter(state, COL_SRC_PORT, port))
                adv_source_menu.addActions([adv_toggle_addr, adv_toggle_client, adv_toggle_port])

                if event_type != NONE:
                    add_event_menu = True
                    adv_event_menu = QtGui.QMenu('{} by event type'.format(enable_str))
                    adv_toggle_event = QtGui.QAction('{} all {} events'.format(enable_str, event_type), self)
                    adv_toggle_event.triggered.connect(lambda _, state=newstate, et=event_type: self.adv_filter(state, filter_type=et))
                    adv_toggle_event_addr = QtGui.QAction('{} all {} events from {}'.format(enable_str, event_type, addr_str), self)
                    adv_toggle_event_addr.triggered.connect(lambda _, state=newstate, addr=addr, et=event_type: self.adv_filter(state, COL_SRC_ADDR, addr, et))
                    adv_toggle_event_client = QtGui.QAction('{} all {} events from client "{}"'.format(enable_str, event_type, client), self)
                    adv_toggle_event_client.triggered.connect(lambda _, state=newstate, client=client, et=event_type: self.adv_filter(state, COL_SRC_CLIENT, client, et))
                    adv_toggle_event_port = QtGui.QAction('{} all {} events from port "{}"'.format(enable_str, event_type, port), self)
                    adv_toggle_event_port.triggered.connect(lambda _, state=newstate, port=port, et=event_type: self.adv_filter(state, COL_SRC_PORT, port, et))
                    adv_event_menu.addActions([adv_toggle_event, adv_toggle_event_addr, adv_toggle_event_client, adv_toggle_event_port])

                    if event_type in [CTRL]:
                        add_ctrl_menu = True
                        param = event.event.data1
                        header = QtGui.QAction('CTRL {}: {}'.format(param, Controllers[param]), self)
                        header.setSeparator(True)
                        adv_param_menu = QtGui.QMenu('{} by {} parameter'.format(enable_str, event_type))
                        adv_param_menu.setSeparatorsCollapsible(False)
                        adv_param = QtGui.QAction('{} all {} {} events'.format(enable_str, event_type, param), self)
                        adv_param.triggered.connect(lambda _, state=newstate, et=event_type, param=param: self.adv_filter(state, filter_type=et, filter_param=param))
                        adv_param_addr = QtGui.QAction('{} all {} {} events from {}'.format(enable_str, event_type, param, addr_str), self)
                        adv_param_addr.triggered.connect(lambda _, state=newstate, addr=addr, et=event_type, param=param: self.adv_filter(state, COL_SRC_ADDR, addr, et, param))
                        adv_param_client = QtGui.QAction('{} all {} {} events from client "{}"'.format(enable_str, event_type, param, client), self)
                        adv_param_client.triggered.connect(lambda _, state=newstate, client=client, et=event_type, param=param: self.adv_filter(state, COL_SRC_CLIENT, client, et, param))
                        adv_param_port = QtGui.QAction('{} all {} {} events from port "{}"'.format(enable_str, event_type, param, port), self)
                        adv_param_port.triggered.connect(lambda _, state=newstate, port=port, et=event_type, param=param: self.adv_filter(state, COL_SRC_PORT, port, et, param))
                        adv_param_menu.addActions([header, adv_param, adv_param_addr, adv_param_client, adv_param_port])
                    else:
                        add_ctrl_menu = False
                else:
                    add_event_menu = False
                    add_ctrl_menu = False
            enabled = all([self.event_buffer[row].enabled for row in selection])
            toggle = QtGui.QAction('{} selected events'.format('Disable' if enabled else 'Enable'), self)
            toggle.triggered.connect(lambda state, s=selection: self.event_enable(s, not enabled))
        if item is not None:
            start = QtGui.QAction('Play from here', self)
            start.triggered.connect(lambda state, index=item.index():self.set_start(item.row(), True))
            menu.addAction(start)
#            sep = QtGui.QAction(self)
#            sep.setSeparator(True)
            menu.addSeparator()
            if selection:
                menu.addAction(toggle)
                if len(selection) == 1:
                    menu.addMenu(adv_source_menu)
                    if add_event_menu:
                        menu.addMenu(adv_event_menu)
                        if add_ctrl_menu:
                            menu.addMenu(adv_param_menu)
                menu.addSeparator()
            col = item.column()
            if col not in [COL_TIME, COL_BBT] and item.text():
                print self.column_data
                print col, self.column_data[col]
                if col == COL_EVENT_TYPE:
                    hide_txt = 'Hide {} events'.format(item.text())
                    show_txt = 'Show only {} events'.format(item.text())
                    show = QtGui.QAction(show_txt, self)
                    show.triggered.connect(lambda: self.filter_set(col, [filter for filter in self.column_data[col] if not filter==str(item.text())], False))
                else:
                    hide_txt = 'Hide events {}'.format(self.txt_str[col].format(item.text()))
                    show_txt = 'Show only events {}'.format(self.txt_str[col].format(item.text()))
                    show = QtGui.QAction(show_txt, self)
                    show.triggered.connect(lambda: self.filter_set(col, [filter for filter in self.column_data[col] if not filter==str(item.text())], False))
                hide = QtGui.QAction(hide_txt, self)
                hide.triggered.connect(lambda: self.filter_set(col, [str(item.text())], False))
                menu.addActions([hide, show])
#            sep = QtGui.QAction(self)
#            sep.setSeparator(True)
            menu.addSeparator()
        elif selection:
            menu.addAction(toggle)
            if len(selection) == 1:
                menu.addMenu(adv_source_menu)
                menu.addMenu(adv_event_menu)
                if add_ctrl_menu:
                    menu.addMenu(adv_param_menu)
#            sep = QtGui.QAction(self)
#            sep.setSeparator(True)
            menu.addSeparator()
        all_enabled = all([data.enabled for data in self.event_buffer])
        if not all_enabled:
            enable = QtGui.QAction('Enable all events', self)
            enable.triggered.connect(lambda: self.event_enable(range(self.model.rowCount()), True))
            menu.addAction(enable)
        filters = any([f for f in self.filter_list if f])
        if filters:
            reset = QtGui.QAction('Show all events', self)
            reset.triggered.connect(self.filter_reset)
            menu.addAction(reset)
        menu.addAction(self.actionExport)
        menu.exec_(self.sender().mapToGlobal(pos))

    def top_header_menu(self, pos):
        col = self.horizontalHeader.logicalIndexAt(pos)
        menu = QtGui.QMenu()

        if col in [COL_TIME, COL_BBT]:
            pass
        elif col == COL_EVENT_TYPE:
            events = (
                      ('All note events', ['NOTEON', 'NOTEOFF']), 
                      ('Note On', ['NOTEON']), 
                      ('Note Off', ['NOTEOFF']), 
                      (None, None), 
                      ('Control', ['CTRL']), 
                      ('SysEx', ['SYSEX']), 
                      ('Program', ['PROGRAM']), 
                      )
            for e_str, e_types in events:
                if e_str is None:
                    sep = QtGui.QAction(self)
                    sep.setSeparator(True)
                    menu.addAction(sep)
                    continue
                action = QtGui.QAction(e_str, self)
                action.setCheckable(True)
                action.setChecked(True if not any([e for e in e_types if e in self.filter_list[col]]) else False)
                action.triggered.connect(lambda state, c=col, e=e_types: self.filter_set(c, e, state))
                menu.addAction(action)
        else:
#            print self.column_data[col]
            for f_str in self.column_data[col]:
                action = QtGui.QAction('Show events {}'.format(self.txt_str[col].format(f_str)), self)
                action.setCheckable(True)
                action.setChecked(True if not f_str in self.filter_list[col] else False)
                action.triggered.connect(lambda state, c=col, e=[f_str]: self.filter_set(c, e, state))
                menu.addAction(action)

        sep = QtGui.QAction(self)
        sep.setSeparator(True)
        menu.addAction(sep)
        reset = QtGui.QAction('Show all events', self)
        reset.triggered.connect(self.filter_reset)
        menu.addAction(reset)
        menu.exec_(self.sender().mapToGlobal(pos))

    def side_header_menu(self, pos):
        conn_events = [index for index, data in enumerate(self.event_buffer) if isinstance(data.event, ConnectionEvent)]
        if not conn_events: return
        row = self.verticalHeader.logicalIndexAt(pos)
        menu = QtGui.QMenu()
        hidden = any([self.event_table.isRowHidden(row) for row in conn_events])
        show_connections = QtGui.QAction('{} connection events'.format('Show' if hidden else 'Hide'), self)
        show_connections.triggered.connect(lambda state: [self.event_table.setRowHidden(row, not hidden) for row in conn_events])
        menu.addAction(show_connections)
        menu.exec_(self.sender().mapToGlobal(pos))
        

    def filter_set(self, col, types, state):
        if not state:
            for t in types:
                self.filter_list[col].add(t)
        else:
            for t in types:
                self.filter_list[col].discard(t)
        print self.filter_list
        self.filterChanged.emit(col, types, state)
        self.row_check()

    def filter_reset(self):
        self.filter_list = [set() for i in self.header_columns]
        self.filterReset.emit()
        self.row_check(force=True)

    def row_check(self, force=False):
        col_list = [i for i, col in enumerate(self.filter_list) if col]
        if not col_list:
            for row in range(self.model.rowCount()):
                if isinstance(self.event_buffer[row].event, ConnectionEvent) and not force: continue
                self.event_table.setRowHidden(row, False)
                self.event_buffer[row].visible = True
            self.model.setHorizontalHeaderLabels(self.header_columns)
            return
        head_check = set()
        for row in range(self.model.rowCount()):
            if isinstance(self.event_buffer[row].event, ConnectionEvent): continue
            hide = False
            for col in col_list:
                if str(self.model.item(row, col).text()) in self.filter_list[col]:
                    hide = True
                    head_check.add(col)
                    break
            self.event_table.setRowHidden(row, hide)
            self.event_buffer[row].visible = not hide
        header = [i for i in self.header_columns]
        for i in head_check:
            header[i] = '{} *'.format(header[i])
        self.model.setHorizontalHeaderLabels(header)

    def repr_time(self, nsecs):
        fs = round(nsecs/(10.**9), 2)
        s = fs%60
        m = int(fs%3600//60)
        h = int(fs//3600)
        return '{:02d}:{:02d}:{:05.2f}'.format(h, m, s)

    def closeEvent(self, event):
        if self.event_buffer not in self.main.inspector_windows:
            res = QtGui.QMessageBox.warning(
                                            self, 'Release this MIDI data?', 
                                            'This MIDI stream is not available in the latest recording any more.\n'+\
                                            'Closing this window will result in losing this wonderful recording FOREVER!!!\n'+\
                                            'Do you want to close anyway?', 
                                            buttons = QtGui.QMessageBox.Ok|QtGui.QMessageBox.No
                                            )
            if res == QtGui.QMessageBox.No:
                event.ignore()
                return
        #we need this because somehow connections and lambda are remembered.
        for data in self.event_buffer.data:
            data.play.disconnect()
        self.stop()
        event.accept()
        self.deleteLater()

class MidiMonitor(QtCore.QObject):
    icon_states = {
                   DISABLED: QtGui.QIcon(':/systray/record-disabled.svg'), 
                   ENABLED: QtGui.QIcon(':/systray/record-enabled.svg'), 
                   ACTIVE: QtGui.QIcon(':/systray/record-active.svg'), 
                   EVENT: QtGui.QIcon(':/systray/record-event.svg'), 
                   SAVED: QtGui.QIcon(':/systray/record-saved.svg'), 
                   }
    def __init__(self, parent=None):
        QtCore.QObject.__init__(self, parent)

        self.cache = str(QtGui.QDesktopServices().storageLocation(QtGui.QDesktopServices.DataLocation))
        if not path.exists(self.cache):
            makedirs(self.cache)

        self.qsettings = QtCore.QSettings()
        self.settings = SettingsObj(self.qsettings)
        self.enabled = self.settings.gGeneral.get_enabled(True, True)
        self.minimum_time = self.settings.gGeneral.get_minimum_time(defaults['minimum_time'], True)
        self.last_event_limit = self.settings.gGeneral.get_last_event_limit(defaults['last_event_limit'], True)
        self.tick_res = self.settings.gGeneral.get_tick_res(defaults['tick_res'], True)
        self.max_rec = self.settings.gGeneral.get_max_rec(defaults['max_rec'], True)
        self.settings.gGeneral.changed_max_rec.connect(self.rec_check)

        self.autosave = self.settings.gGeneral.get_autosave(False, True)
        self.autosave_path = self.settings.gGeneral.get_autosave_path(None)
        self.settings.gGeneral.changed_autosave.connect(lambda state: setattr(self, 'autosave', state))
        self.settings.gGeneral.changed_autosave_path.connect(lambda path: setattr(self, 'autosave_path', path))

        self.event_type_filter = self.settings.gFilters.get_event_type(set(), False)
        self.client_id_filter = self.settings.gFilters.get_client_id(set(), False)
        self.port_id_filter = self.settings.gFilters.get_port_id(set(), False)
        self.port_name_filter = self.compile_port_filter_regex(self.settings.gFilters.get_port_name(set(), False))
        self.settings.gFilters.changed_event_type.connect(lambda filter: setattr(self, 'event_type_filter', filter if filter else set()))
        self.settings.gFilters.changed_client_id.connect(lambda filter: setattr(self, 'client_id_filter', filter if filter else set()))
        self.settings.gFilters.changed_port_id.connect(lambda filter: setattr(self, 'port_id_filter', filter if filter else set()))
        self.settings.gFilters.changed_port_name.connect(lambda filter: setattr(self, 'port_name_filter', self.compile_port_filter_regex(filter if filter is not None else set())))

        self.settings_dialog = SettingsDialog(self)
        self.trayicon = QtGui.QSystemTrayIcon(self.icon_states[ENABLED], parent)
        self.trayicon.show()
        self.trayicon.activated.connect(self.show_menu)

        self.icon_timer = QtCore.QTimer()
        self.icon_timer.setInterval(200)
        self.icon_timer.setSingleShot(True)
        self.icon_timer.timeout.connect(lambda: self.icon_set(ACTIVE))
        self.icon_timer_saved = QtCore.QTimer()
        self.icon_timer_saved.setInterval(10000)
        self.icon_timer_saved.setSingleShot(True)
        self.icon_timer_saved.timeout.connect(lambda: self.icon_set(ENABLED))
        

        self.alsa_thread = QtCore.QThread()
        self.alsa = AlsaMidi(self)
        self.alsa.moveToThread(self.alsa_thread)
        self.alsa.stopped.connect(self.alsa_thread.quit)
        self.alsa_thread.started.connect(self.alsa.run)
        self.alsa.midi_signal.connect(self.alsa_midi_event)
        self.alsa.port_start.connect(self.new_alsa_port)
        self.alsa.conn_register.connect(self.alsa_conn_event)
        self.alsa_thread.start()
        self.seq = self.alsa.seq
        self.input = self.alsa.input
        self.graph = self.alsa.graph
        self.port_discovery()

        self.timer = QtCore.QElapsedTimer()
        self.last_event_timer = QtCore.QTimer()
        self.last_event_timer.setInterval(self.last_event_limit*1000)
        self.last_event_timer.setSingleShot(True)
        self.last_event_timer.timeout.connect(self.last_event_timeout)
        self.settings.gGeneral.changed_last_event_limit.connect(lambda v: setattr(self, 'last_event_limit', v))
        self.settings.gGeneral.changed_last_event_limit.connect(lambda v: self.last_event_timer.setInterval(v*1000))

        self.load_stored_rec()
        self.event_buffer = MidiStream()
        self.inspector_windows = {}

    def compile_port_filter_regex(self, name_list):
        return re.compile('^{}$'.format('$|^'.join([name for name in name_list])))

    def rec_check(self, value):
        self.max_rec = value
        while len(self.rec_list) > self.max_rec:
            self.rec_delete(0)

    def load_stored_rec(self):
        self.rec_list = []
        file_regex = re.compile(r'((?:[0-9]{4}){1}?(?:(?:0[1-9])|1[0-2]){1}?(?:(?:0[1-9])|(?:[12][0-9])|(?:3[0-1]))(?:[01][0-9]|2[0-3]){1}(?:[0-5][0-9]){2}?)_mididata')
        discard = []
        for i, f in enumerate(sorted(glob(path.join(self.cache, '*_mididata')), reverse=True)):
            file_name = path.basename(f)
            match = file_regex.match(file_name)
            if match is None: continue
            if i >= self.max_rec:
                discard.append(f)
                continue
            try:
                with open(f, 'rb') as d:
                    stream = MidiStream.unserialize(json.load(d))
                stream.file_name = file_name
                self.rec_list.append(stream)
            except Exception as err:
                print '"{}" is unreadable and scheduled for removal, reason: {}'.format(path.basename(f), err)
                discard.append(f)
        for f in discard:
            try:
                remove(f)
            except:
                print 'Unable to remove file "{}"'.format(f)
        self.rec_list.reverse()


    def icon_set(self, state=ENABLED):
        self.trayicon.setIcon(self.icon_states[state])

    def show_menu(self, reason):
        if not reason == QtGui.QSystemTrayIcon.Context: return
        QtGui.QIcon.setThemeName(QtGui.QApplication.style().objectName())
        menu = QtGui.QMenu()
        menu.setSeparatorsCollapsible(False)

        header = QtGui.QAction('MidiMemo', self)
        header.setSeparator(True)
        menu.addAction(header)
        toggle = QtGui.QAction('&Enable', self)
        toggle.setCheckable(True)
        toggle.setChecked(True if self.enabled else False)
        toggle.triggered.connect(self.enable_set)
        menu.addAction(toggle)

        if self.rec_list:
            title = QtGui.QAction('Recordings:', self)
            title.setSeparator(True)
            menu.addAction(title)
            old_max = 10
            if len(self.rec_list) > old_max:
                older = QtGui.QMenu('Older recordings ({})'.format(len(self.rec_list)-old_max))
                menu.addMenu(older)
                sep = QtGui.QAction(self)
                sep.setSeparator(True)
                menu.addAction(sep)
            for i, entry in enumerate(self.rec_list):
                rec = QtGui.QMenu(str(entry), menu)
                play = QtGui.QAction('Play', self)
                play.triggered.connect(lambda trig, e=entry: self.new_inspector(e, True))
                inspect = QtGui.QAction('Inspect', self)
                inspect.triggered.connect(lambda trig, e=entry: self.new_inspector(e, False))
                sep = QtGui.QAction(self)
                sep.setSeparator(True)
                delete = QtGui.QAction('Delete', self)
                delete.triggered.connect(lambda trig, id=i: self.rec_delete(id))
                rec.addActions([play, inspect, sep, delete])
                if i < len(self.rec_list)-old_max:
                    older.addMenu(rec)
                else:
                    menu.addMenu(rec)

        sep = QtGui.QAction(self)
        sep.setSeparator(True)
        settings = QtGui.QAction('&Settings...', self)
        settings.triggered.connect(self.settings_dialog.show)
        sep2 = QtGui.QAction(self)
        sep2.setSeparator(True)
        quit_item = QtGui.QAction('&Quit', self)
        quit_item.setIcon(QtGui.QIcon.fromTheme('application-exit'))
        quit_item.triggered.connect(self.quit)
        menu.addActions([sep, settings, sep2, quit_item])
        menu.exec_(QtGui.QCursor.pos())

    def rec_delete(self, id):
        rec = self.rec_list.pop(id)
        try:
            remove(path.join(self.cache, rec.file_name))
            if rec in self.inspector_windows:
                self.inspector_windows.pop(rec)
        except Exception as err:
            print 'There was a problem trying to delete an old file: {}'.format(err)
        del rec

    def new_inspector(self, entry, play=False):
        def remove(item):
            try:
                self.inspector_windows.pop(item)
            except:
                pass
        if not entry in self.inspector_windows.keys():
            inspector = MidiInspector(self, entry, play)
            self.inspector_windows[entry] = inspector
            inspector.destroyed.connect(lambda win, e=entry: remove(e))
        else:
            self.inspector_windows[entry].activate(play)

    def enable_set(self, state):
        self.enabled = state
        self.icon_set(state)

    def port_discovery(self):
        for client_id, port_dict in self.graph.port_id_dict.items():
            if client_id == 0: continue
            for port_id, port in port_dict.items():
                if port.is_output and port != self.alsa.output and not alsaseq.SEQ_PORT_CAP_NO_EXPORT in port.caps:
                    self.seq.connect_ports(port.addr, self.input.addr)

    def new_alsa_port(self, port):
        if not port.is_output or alsaseq.SEQ_PORT_CAP_NO_EXPORT in port.caps: return
        try:
            self.seq.connect_ports(port.addr, self.input.addr)
        except Exception as err:
            print 'Wow! {}'.format(err)


    def alsa_midi_event(self, event):
        if not self.enabled: return
        if not self.event_buffer:
            self.timer.start()
        time = self.timer.nsecsElapsed()
        client_id, port_id = map(int, event.source)
        client_name = str(self.graph.client_id_dict[client_id])
        port_name = str(self.graph.port_id_dict[client_id][port_id])
        if event.type in self.event_type_filter or\
                client_id in self.client_id_filter or\
                (client_id, port_id) in self.port_id_filter or\
                self.port_name_filter.match('{}:{}'.format(client_name, port_name)):
            return
        source = MidiSource(client_name, port_name, (client_id, port_id))
        print 'T: {} ({}) > {}'.format(self.timer.nsecsElapsed(), event, event.dest)
        self.event_buffer.append(event, time, source)
        self.last_event_timer.start()
        self.icon_set(EVENT)
        self.icon_timer.start()
        self.icon_timer_saved.stop()

    def alsa_conn_event(self, conn, state):
        print 'connection {}: src({}:{})'.format('created' if state else 'lost', conn.dest.client.name, conn.dest.name)
        if not (self.enabled and self.event_buffer): return
        time = self.timer.nsecsElapsed()
        source_tuple = (conn.src.client.name, conn.src.name, conn.src.addr)
        source = MidiSource(*source_tuple)
        dest_tuple = (conn.dest.client.name, conn.dest.name, conn.dest.addr)
        event = ConnectionEvent(source_tuple, dest_tuple, state)
        self.event_buffer.append(event, time, source)
        self.last_event_timer.start()
        self.icon_set(EVENT)
        self.icon_timer.start()
        self.icon_timer_saved.stop()

    def last_event_timeout(self):
        if self.timer.nsecsElapsed()/(10**9) < (self.last_event_limit+self.minimum_time):
            self.icon_set()
            self.event_buffer = MidiStream()
            return
        self.icon_set(SAVED)
        self.icon_timer_saved.start()
        self.save_buffer()

    def save_buffer(self):
        def get_name():
            now = QtCore.QDateTime.currentDateTime()
            date = now.date()
            time = now.time()
            s = ''.join(map('{:02d}'.format, [date.year(), date.month(), date.day(), time.hour(), time.minute(), time.second()]))
            return '{}_mididata'.format(s)
        self.event_buffer.close(self.last_event_limit)
        file_name = get_name()
        with open(path.join(self.cache, file_name), 'wb') as f:
            json.dump(self.event_buffer.serialize(), f)
        self.event_buffer.file_name = file_name
        self.rec_list.append(self.event_buffer)
        self.event_buffer = MidiStream()
        while len(self.rec_list) > self.max_rec:
            buffer = self.rec_list.pop(0)
            try:
                self.inspector_windows.pop(buffer)
            except:
                pass

    def quit(self):
        QtGui.QApplication.quit()

def main():
    app = QtGui.QApplication(sys.argv)
    app.setOrganizationName('jidesk')
    app.setApplicationName('MidiMemo')
    app.setQuitOnLastWindowClosed(False)
    MidiMonitor(app)
    sys.exit(app.exec_())

if __name__ == '__main__':
    main()



