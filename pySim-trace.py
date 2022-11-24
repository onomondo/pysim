#!/usr/bin/env python3

import sys
import logging, colorlog
import argparse
from pprint import pprint as pp

from pySim.apdu import *
from pySim.filesystem import RuntimeState

from pySim.cards import UsimCard
from pySim.commands import SimCardCommands
from pySim.profile import CardProfile
from pySim.ts_102_221 import CardProfileUICCSIM
from pySim.ts_31_102 import CardApplicationUSIM
from pySim.ts_31_103 import CardApplicationISIM
from pySim.transport import LinkBase

from pySim.apdu_source.gsmtap import GsmtapApduSource
from pySim.apdu_source.pyshark_rspro import PysharkRsproPcap, PysharkRsproLive

from pySim.apdu.ts_102_221 import UiccSelect, UiccStatus

log_format='%(log_color)s%(levelname)-8s%(reset)s %(name)s: %(message)s'
colorlog.basicConfig(level=logging.INFO, format = log_format)
logger = colorlog.getLogger()

# merge all of the command sets into one global set. This will override instructions,
# the one from the 'last' set in the addition below will prevail.
from pySim.apdu.ts_51_011 import ApduCommands as SimApduCommands
from pySim.apdu.ts_102_221 import ApduCommands as UiccApduCommands
from pySim.apdu.ts_31_102 import ApduCommands as UsimApduCommands
from pySim.apdu.global_platform import ApduCommands as GpApduCommands
ApduCommands = SimApduCommands + UiccApduCommands + UsimApduCommands #+ GpApduCommands


class DummySimLink(LinkBase):
    """A dummy implementation of the LinkBase abstract base class.  Currently required
    as the UsimCard doesn't work without SimCardCommands, which in turn require
    a LinkBase implementation talking to a card.

    In the tracer, we don't actually talk to any card, so we simply drop everything
    and claim it is successful.

    The UsimCard / SimCardCommands should be refactored to make this obsolete later."""
    def __init__(self, debug: bool = False, **kwargs):
        super().__init__(**kwargs)
        self._debug = debug
        self._atr = h2i('3B9F96801F878031E073FE211B674A4C753034054BA9')

    def _send_apdu_raw(self, pdu):
        #print("DummySimLink-apdu: %s" % pdu)
        return [], '9000'

    def connect(self):
        pass

    def disconnect(self):
        pass

    def reset_card(self):
        return 1

    def get_atr(self):
        return self._atr

    def wait_for_card(self):
        pass


class Tracer:
    def __init__(self, **kwargs):
        # we assume a generic SIM + UICC + USIM + ISIM card
        profile = CardProfileUICCSIM()
        profile.add_application(CardApplicationUSIM())
        profile.add_application(CardApplicationISIM())
        scc = SimCardCommands(transport=DummySimLink())
        card = UsimCard(scc)
        self.rs = RuntimeState(card, profile)
        # APDU Decoder
        self.ad = ApduDecoder(ApduCommands)
        # parameters
        self.suppress_status = kwargs.get('suppress_status', True)
        self.suppress_select = kwargs.get('suppress_select', True)
        self.source = kwargs.get('source', None)

    def format_capdu(self, inst: ApduCommand):
        """Output a single decoded + processed ApduCommand."""
        print("%02u %-16s %-35s %-8s %s %s" % (inst.lchan_nr, inst._name, inst.path_str, inst.col_id, inst.col_sw, inst.processed))
        print("===============================")

    def main(self):
        """Main loop of tracer: Iterates over all Apdu received from source."""
        while True:
            # obtain the next APDU from the source (blocking read)
            apdu = self.source.read()
            #print(apdu)

            if isinstance(apdu, CardReset):
                self.rs.reset()
                continue

            # ask ApduDecoder to look-up (INS,CLA) + instantiate an ApduCommand derived
            # class like 'UiccSelect'
            inst = self.ad.input(apdu)
            # process the APDU (may modify the RuntimeState)
            inst.process(self.rs)

            # Avoid cluttering the log with too much verbosity
            if self.suppress_select and isinstance(inst, UiccSelect):
                continue
            if self.suppress_status and isinstance(inst, UiccStatus):
                continue
            #print(inst)
            self.format_capdu(inst)

option_parser = argparse.ArgumentParser(prog='pySim-trace', description='Osmocom pySim high-level SIM card trace decoder',
                                        formatter_class=argparse.ArgumentDefaultsHelpFormatter)

global_group = option_parser.add_argument_group('General Options')
global_group.add_argument('--no-suppress-select', action='store_false', dest='suppress_select',
                          help="Don't suppress displaying SELECT APDUs")
global_group.add_argument('--no-suppress-status', action='store_false', dest='suppress_status',
                          help="Don't suppress displaying STATUS APDUs")

subparsers = option_parser.add_subparsers(help='APDU Source', dest='source', required=True)

parser_gsmtap = subparsers.add_parser('gsmtap-udp', help='Live capture of GSMTAP-SIM on UDP port')
parser_gsmtap.add_argument('-i', '--bind-ip', default='127.0.0.1',
                           help='Local IP address to which to bind the UDP port')
parser_gsmtap.add_argument('-p', '--bind-port', default=4729,
                           help='Local UDP port')

parser_rspro_pyshark_pcap = subparsers.add_parser('rspro-pyshark-pcap', help="""
    PCAP file containing RSPRO (osmo-remsim) communication; processed via pyshark.
    REQUIRES OSMOCOM PATCHED WIRESHARK!""")
parser_rspro_pyshark_pcap.add_argument('-f', '--pcap-file', required=True,
                                       help='Name of the PCAP[ng] file to be read')

parser_rspro_pyshark_live = subparsers.add_parser('rspro-pyshark-live', help="""
    Live capture of RSPRO (osmo-remsim) communication; processed via pyshark.
    REQUIRES OSMOCOM PATCHED WIRESHARK!""")
parser_rspro_pyshark_live.add_argument('-i', '--interface', required=True,
                                       help='Name of the network interface to capture on')

if __name__ == '__main__':

    opts = option_parser.parse_args()

    logger.info('Opening source %s...' % opts.source)
    if opts.source == 'gsmtap-udp':
        s = GsmtapApduSource(opts.bind_ip, opts.bind_port)
    elif opts.source == 'rspro-pyshark-pcap':
        s = PysharkRsproPcap(opts.pcap_file)
    elif opts.source == 'rspro-pyshark-live':
        s = PysharkRsproLive(opts.interface)

    tracer = Tracer(source=s, suppress_status=opts.suppress_status, suppress_select=opts.suppress_select)
    logger.info('Entering main loop...')
    tracer.main()

