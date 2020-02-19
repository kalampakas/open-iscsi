"""
harness stuff (support) -- utility routines
"""

import os
import shutil
import sys
import unittest

#
# globals
#
class Global:
    _FSTYPE = os.getenv('FSTYPE', 'ext3')
    _MOUNTOPTIONS = os.getenv('MOUNTOPTIONS', '').split(' ') + [' -t ', _FSTYPE]
    _MKFSCMD = [os.getenv('MKFSCMD', 'mkfs.' + _FSTYPE)]
    if os.getenv('MKFSOPTS'):
        _MKFSCMD += os.getenv('MKFSOPTS').split(' ')
    _PARTITIONSUFFIX = '1'
    _BONNIEPARAMS = os.getenv('BONNIEPARAMS', '--r0 -n10:0:0 -s16 -uroot -f -q').split(' ')
    verbosity = 1
    debug = False
    # the target (e.g. "iqn.*")
    target = None
    # the IP and optional port (e.g. "linux-system", "192.168.10.1:3260")
    ipnr = None
    # the device that will be created when our target is connected
    device = None
    # the first and only partition on said device
    partition = None
    # optional override for fio disk testing block size(s)
    blocksize = None


def dprint(*args):
    """
    Print a debug message if in debug mode
    """
    if Global.debug:
        print('DEBUG: ', file=sys.stderr, end='')
        for arg in args:
            print(arg, file=sys.stderr, end='')
        print('', file=sys.stderr)

def vprint(*args):
    """
    Print a verbose message
    """
    if Global.verbosity > 1 and args:
        for arg in args:
            print(arg, end='')
        print('')

def notice(*args):
    """
    Print if not in quiet mode
    """
    if Global.verbosity > 0:
        for arg in args:
            print(arg, end='')
        print('')

def run_cmd(cmd, output_save_file=None, quiet_mode=False):
    """
    run specified command, waiting for and returning result
    """
    if (Global.verbosity > 1 and not quiet_mode) or Global.debug:
        cmd_str = ' '.join(cmd)
        if output_save_file:
            cmd_str += ' >& %s' % output_save_file
        vprint(cmd_str)
    pid = os.fork()
    if pid < 0:
        print("Error: cannot fork!", flie=sys.stderr)
        sys.exit(1)
    if pid == 0:
        # the child
        if output_save_file or quiet_mode:
            stdout_fileno = sys.stdout.fileno()
            stderr_fileno = sys.stderr.fileno()
            if output_save_file:
                new_stdout = os.open(output_save_file, os.O_WRONLY|os.O_CREAT|os.O_TRUNC,
                                     mode=0o664)
            else:
                new_stdout = os.open('/dev/null', os.O_WRONLY)
            os.dup2(new_stdout, stdout_fileno)
            os.dup2(new_stdout, stderr_fileno)
        os.execvp(cmd[0], cmd)
        # not reached

    # the parent
    wpid, wstat = os.waitpid(pid, 0)
    if wstat != 0:
        dprint("exit status: (%d) %d" % (wstat, os.WEXITSTATUS(wstat)))
    return os.WEXITSTATUS(wstat)

def new_initArgParsers(self):
    """
    Add  some options to the normal unittest main options
    """
    global old_initArgParsers

    old_initArgParsers(self)
    self._main_parser.add_argument('-d', '--debug', dest='debug',
            action='store_true',
            help='Enable developer debugging')
    self._main_parser.add_argument('-t', '--target', dest='target',
            action='store',
            help='Required: target name')
    self._main_parser.add_argument('-i', '--ipnr', dest='ipnr',
            action='store',
            help='Required: name-or-ip[:port]')
    self._main_parser.add_argument('-D', '--device', dest='device',
            action='store',
            help='Required: device')
    self._main_parser.add_argument('-B', '--blocksize', dest='blocksize',
            action='store',
            help='block size (defaults to an assortment of sizes)')

def new_parseArgs(self, argv):
    """
    Gather globals from unittest main for local consumption
    """
    global old_parseArgs

    old_parseArgs(self, argv)
    Global.verbosity = self.verbosity
    Global.debug = self.debug
    for v in ['target', 'ipnr', 'device']:
        if getattr(self, v) is None:
            print('Error: "%s" required' % v.upper())
            sys.exit(1)
        setattr(Global, v, getattr(self, v))
    Global.blocksize = self.blocksize
    dprint("found: verbosity=%d, target=%s, ipnr=%s, device=%s, bs=%s" % \
            (Global.verbosity, Global.target, Global.ipnr, Global.device, Global.blocksize))
    # get partition from path
    device_dir = os.path.dirname(Global.device)
    if device_dir == '/dev':
        Global.partition = '%s1' % Global.device
    elif device_dir in ['/dev/disk/by-id', '/dev/disk/by-path']:
        Global.partition = '%s-part1' % Global.device
    else:
        print('Error: must start with "/dev" or "/dev/disk/by-{id,path}": %s' % \
                Global.device, file=sys.sttderr)
        sys.exit(1)

def setup_testProgram_overrides():
    """
    Add in special handling for a couple of the methods in TestProgram (main)
    so that we can add parameters and detect some globals we care about
    """
    global old_parseArgs, old_initArgParsers

    old_initArgParsers = unittest.TestProgram._initArgParsers
    unittest.TestProgram._initArgParsers = new_initArgParsers
    old_parseArgs = unittest.TestProgram.parseArgs
    unittest.TestProgram.parseArgs = new_parseArgs


def verify_needed_commands_exist(cmd_list):
    """
    Verify that the commands in the supplied list are in our path
    """
    path_list = os.getenv('PATH').split(':')
    for cmd in cmd_list:
        found = False
        for a_path in path_list:
            if os.path.exists('%s/%s' % (a_path, cmd)):
                found = True
                break
        if not found:
            print('Error: %s must be in your PATH' % cmd)
            sys.exit(1)


def run_fio():
    """
    Run the fio benchmark for various block sizes.
    
    Return zero for success.
    Return non-zero for failure and a failure reason.

    Uses Globals: device, blocksize
    """
    if Global.blocksize is not None:
        dprint('Found a block size passed in: %s' % Global.blocksize)
        blocksizes = Global.blocksize.split(' ')
    else:
        dprint('NO Global block size pass in?')
        blocksizes = ['512', '1k', '2k', '4k', '8k',
                '16k', '32k', '75536', '128k', '1000000']
    # for each block size, do a read test, then a write test
    for bs in blocksizes:
        vprint('Running "fio" read test: 2 seconds, 8 threads, bs=%s' % bs)
        res = run_cmd(['fio', '--name=read-test', '--readwrite=randread',
            '--runtime=2s', '--numjobs=8', '--blocksize=%s' % bs, 
            '--direct=1', '--filename=%s' % Global.device], quiet_mode=True)
        if res != 0:
            return (res, 'fio failed')
        vprint('Running "fio" write test: 2 seconds, 8 threads, bs=%s' % bs)
        res = run_cmd(['fio', '--name=write-test', '--readwrite=randwrite',
            '--runtime=2s', '--numjobs=8', '--blocksize=%s' % bs, 
            '--direct=1', '--filename=%s' % Global.device], quiet_mode=True)
        if res != 0:
            return (res, 'fio failed')
        vprint('Running "fio" verify test: 2 seconds, 8 threads, bs=%s' % bs)
        res = run_cmd(['fio', '--name=verify-test', '--readwrite=randwrite',
            '--runtime=2s', '--numjobs=1', '--blocksize=%s' % bs, 
            '--direct=1', '--filename=%s' % Global.device,
            '--verify=md5', '--verify_state_save=0'], quiet_mode=True)
        if res != 0:
            return (res, 'fio failed')
    return (0, None)

def run_parted():
    """
    Run the parted program to ensure there is one partition,
    and that it covers the whole disk

    Return zero for success and the device pathname.
    Return non-zero for failure and a failure reason.

    Uses Globals: device, partition
    """
    # zero out the label and parition table
    res = run_cmd(['dd', 'if=/dev/zero', 'of=%s' % Global.device, 'bs=4k', 'count=100'],
            quiet_mode=True)
    if res != 0:
        return (res, '%s: could not zero out label' % Global.device)
    # ensure our partition file is not there, to be safe
    if os.path.exists(Global.partition):
        return (1, '%s: Partition already exists?' % Global.partition)
    # make a label, then a partition table with one partition
    res = run_cmd(['parted', Global.device, 'mklabel', 'gpt'], quiet_mode=True)
    if res != 0:
        return (res, '%s: Could not create a GPT label' % Global.device)
    res = run_cmd(['parted', '-a', 'none', Global.device, 'mkpart', 'primary', '0', '100%'],
            quiet_mode=True)
    if res != 0:
        return (res, '%s: Could not create a primary partition' % Global.device)
    # wait for the partition to show up
    for i in range(10):
        if os.path.exists(Global.partition):
            break
        os.sleep(1)
    if not os.path.exists(Global.partition):
        return (1, '%s: Partition never showed up?' % Global.partition)
    return (0, None)

def run_mkfs():
    #return (1, "Not Yet Implemented: mkfs")
    return (0, None)

def run_bonnie():
    #return (1, "Not Yet Implemented: bonnie")
    return (0, None)
