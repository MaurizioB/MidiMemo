MidiMemo
========

MidiMemo is a GNU/Linux automatic MIDI recorder. This is a test release,
some features still need to be fixed/enabled.

Features
--------

-  Automatically record every MIDI event
-  Stays in the background, automatically connects new midi ports
-  Inspect and filter the midi events
-  Export to .MID midi file, with filtering

Requirements
------------

-  Python 2.7
-  PyQt4 at least version 4.11.1
-  pyalsa

Usage
-----

There is not an installation procedure yet, just run the script in the
main directory:

::

    $ ./MidiMemo

After that, MidiMemo will be in the system tray, showing received events
from ALSA devices (keyboards, sequencers, etc).

The dark red icon shows that recording is active, bright red are actual
MIDI events, while the green icon indicates that the latest "stream" has
been saved in the recording list and can be inspected/exported.

Future
------

-  JACK support.
-  Automatic export of MIDI files everytime a "stream" ends.
-  MIDI output device selection in the inspector window.
-  Full MIDI "replay" in the inspector window, trying to reproduce the
   actual performance according to MIDI connections enabled/disabled
   during it.

Known issues
------------

Due to a strange bug I'm inspecting about, tracks with both sysex and
normal events generate a malformed midi files. For the time being, if
you need to export such data, export the single track with sysex events
without them, then export as another data with sysex only. Playing from
the inspector window is supported, but you will have to manually connect
the MidiMemo midi port to a midi device.
