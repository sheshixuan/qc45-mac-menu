"""Contract tests using captured QC45 replies and a fake wire boundary."""
import json
from pathlib import Path
import tempfile
import unittest

from i18n import set_language
from qc45_settings import DeviceSettings, SettingsError, parse_voice, parse_standby

VOICE = bytes.fromhex('e10001815e0000')
SLOT = bytes.fromhex('0300000101000000000000000000000000000000000000000000000000000000000000000000000000090000000000')

def reply(g, f, p, op=3):
    return bytes([g, f, op, len(p)]) + p

class Wire:
    """Emulates only device I/O; codecs, validation, backup and checks stay real."""
    def __init__(self):
        self.state = {(1,3): VOICE, (1,4): b'\0', (1,11): b'\1\2\x0f',
                      (31,6): SLOT, (1,7): bytes.fromhex('f60a0800f60a0001f60a0002'),
                      (31,3): b'\0', (2,2): b'\x3c\xff\xff\0'}
        self.writes = []
        self.reject = False
        self.ignore = False
        self.unrelated = False
    def __call__(self, packet):
        g,f,op,n = packet[:4]
        data=packet[4:]
        assert n==len(data)
        if op in (2,5):
            self.writes.append(packet)
            if self.reject:
                return reply(g,f,b'\x05',4)
            if not self.ignore:
                if (g,f)==(1,3):
                    old=self.state[g,f]
                    self.state[g,f]=bytes([(old[0]&0xc0)|(data[0]&0x3f)])+old[1:6]+(data[1:2] or old[6:7])
                elif (g,f)==(1,11):
                    self.state[g,f]=data+self.state[g,f][2:]
                elif (g,f)==(1,4):
                    self.state[g,f]=data if len(data)==1 else bytes([data[0],0,data[1]])
                elif (g,f)==(31,6):
                    old=bytearray(self.state[g,f]); old[0:3]=data[0:3]
                    old[6:38]=data[3:35]; old[42:45]=data[35:38]; old[46]=data[38]
                    self.state[g,f]=bytes(old)
                elif (g,f)==(1,7):
                    old=bytearray(self.state[g,f]); old[data[1]*4+2]=data[0]; self.state[g,f]=bytes(old)
                elif (g,f)==(31,3):
                    self.state[g,f]=data[:1]
        prefix=reply(2,2,b'\x3c\xff\xff\0') if self.unrelated else b''
        return prefix+reply(g,f,self.state[g,f])

class SettingsTests(unittest.TestCase):
    def setUp(self):
        set_language("zh-Hans")
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.wire=Wire(); self.dev=DeviceSettings(self.wire,backup_dir=self.tmp.name)
    def test_voice_language_mask_is_big_endian(self):
        v=parse_voice(VOICE)
        self.assertEqual(v['language'],1)
        self.assertEqual(v['languages'],[1,2,3,4,6,8,15,16])
        self.assertTrue(v['enabled']); self.assertTrue(v['can_toggle'])
    def test_language_change_keeps_disabled_prompts_and_battery_option(self):
        self.wire.state[1,3]=bytes.fromhex('c10001815e0101')
        self.dev.set_voice(language=8)
        self.assertEqual(self.wire.writes[-1],bytes.fromhex('010302020801'))
        v=self.dev.read_voice(); self.assertFalse(v['enabled']); self.assertEqual(v['language'],8)
    def test_prompt_toggle_keeps_language(self):
        self.wire.state[1,3]=bytes.fromhex('e80001815e0000')
        self.dev.set_voice(enabled=False)
        self.assertEqual(self.wire.writes[-1],bytes.fromhex('0103020108'))
    def test_unsupported_language_does_not_write(self):
        with self.assertRaises(SettingsError): self.dev.set_voice(language=9)
        self.assertEqual(self.wire.writes,[])
    def test_sidetone_write_is_persisted_and_read_back(self):
        self.dev.set_sidetone(3)
        self.assertEqual(self.wire.writes[-1],bytes.fromhex('010b02020103'))
        self.assertEqual(self.dev.read_sidetone()['level'],3)
    def test_long_timer_requires_reported_wide_format(self):
        with self.assertRaises(SettingsError): self.dev.set_standby(1440)
        self.assertEqual(self.wire.writes,[])
        self.wire.state[1,4]=bytes.fromhex('000000')
        self.dev.set_standby(1440)
        self.assertEqual(self.wire.writes[-1],bytes.fromhex('01040202a005'))
        self.assertEqual(parse_standby(self.wire.state[1,4])['minutes'],1440)
    def test_standby_roundtrip_zero_and_twenty(self):
        self.dev.set_standby(20)
        self.assertEqual(self.dev.read_standby()['minutes'],20)
        self.dev.set_standby(0)
        self.assertEqual(self.dev.read_standby()['minutes'],0)
    def test_wind_preserves_opaque_name_prompt_and_other_fields(self):
        raw=bytearray(SLOT);raw[1:3]=b'\0\x08';raw[6:38]=bytes(range(32));raw[42]=6
        self.wire.state[31,6]=bytes(raw)
        self.dev.set_wind(True,3)
        got=self.wire.writes[-1]
        self.assertEqual(got[:4],bytes.fromhex('1f060227'))
        self.assertEqual(got[4:7],b'\3\0\x08');self.assertEqual(got[7:39],bytes(range(32)))
        self.assertEqual(got[39:],b'\0\0\0\1')
        self.assertTrue(self.dev.read_slot(3)['wind'])
    def test_preset_is_not_editable_even_with_wind_capability(self):
        raw=bytearray(SLOT);raw[0]=0;raw[3]=0
        self.wire.state[31,6]=bytes(raw)
        with self.assertRaises(SettingsError): self.dev.set_wind(True,0)
        self.assertEqual(self.wire.writes,[])
    def test_device_rejection_and_ignored_write_do_not_report_success(self):
        self.wire.reject=True
        with self.assertRaises(SettingsError): self.dev.set_standby(20)
        self.wire.reject=False;self.wire.ignore=True
        with self.assertRaises(SettingsError): self.dev.set_standby(20)
    def test_missing_write_ack_is_resolved_by_readback(self):
        base=self.wire
        def no_ack(packet):
            result=base(packet)
            return b'' if packet[2]==2 else result
        self.dev.exchange=no_ack
        self.dev.set_voice(enabled=False)
        self.assertFalse(self.dev.read_voice()['enabled'])
        self.wire.ignore=True
        with self.assertRaises(SettingsError): self.dev.set_voice(enabled=True)
    def test_backup_exists_before_write_and_contains_restore_packet(self):
        base=self.dev.exchange
        def checked(packet):
            if packet[2]==2:
                files=list(Path(self.tmp.name).glob('*.json'));self.assertTrue(files)
                backup=json.loads(files[-1].read_text())
                self.assertEqual(backup['restore_request_hex'],'0104020100')
            return base(packet)
        self.dev.exchange=checked
        self.dev.set_standby(20)
    def test_backup_failure_prevents_write(self):
        path=Path(self.tmp.name)/'not-a-directory';path.write_text('occupied')
        self.dev.backup_dir=path
        with self.assertRaises(OSError): self.dev.set_standby(20)
        self.assertEqual(self.wire.writes,[])
    def test_matching_reply_skips_unrelated_notifications(self):
        self.wire.unrelated=True
        self.assertEqual(self.dev.read_voice()['language'],1)
    def test_truncated_reply_is_not_a_setting(self):
        self.dev.exchange=lambda packet:bytes.fromhex('01030307e1')
        with self.assertRaises(SettingsError): self.dev.read_voice()
    def test_eq_negative_write_and_readback(self):
        self.dev.set_eq(2,-6)
        self.assertEqual(self.wire.writes[-1],bytes.fromhex('01070202fa02'))
        self.assertEqual(self.dev.read_eq()[2],-6)
    def test_error_text_follows_the_interface_language(self):
        set_language("en")
        self.addCleanup(lambda: set_language("zh-Hans"))
        with self.assertRaises(SettingsError) as caught:
            self.dev.set_cnc(11, 3)
        self.assertIn("0 to 10", str(caught.exception))
        self.assertNotIn("范围", str(caught.exception))

    def test_cnc_writes_clear_wind_and_preserve_mode(self):
        raw=bytearray(SLOT);raw[46]=1;self.wire.state[31,6]=bytes(raw)
        self.dev.set_cnc(6,3)
        self.assertEqual(self.wire.writes[-1][-4:],bytes([6,0,0,0]))
        self.assertEqual(self.dev.read_slot(3)['cnc'],6)

if __name__=='__main__': unittest.main()
