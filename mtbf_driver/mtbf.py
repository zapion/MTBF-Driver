import os
import sys
import os.path
import signal
import time
import json
import re
import shutil
from zipfile import ZipFile
from ConfigParser import NoSectionError
from gaiatest.runtests import GaiaTestRunner, GaiaTestOptions
from mozlog import structured
from mozdevice.devicemanager import DMError
from utils.memory_report_args import memory_report_args
from utils.step_gen import RandomStepGen, ReplayStepGen
from utils.time_utils import time2sec
import utils.crash_scan as CrashScan

import mozversion


class MTBFTestRunner(GaiaTestRunner):

    saved_version_info = None

    def get_version_info(self, input_version_info=None):
        if input_version_info is None:
            self.saved_version_info = mozversion.get_version(binary=self.bin,
                                                             sources=self.sources,
                                                             dm_type=os.environ.get('DM_TRANS', 'adb'),
                                                             device_serial=self.device_serial)
            mozversion.get_version = self._new_get_version_info
        else:
            self.saved_version_info = input_version_info
            mozversion.get_version = self._new_get_version_info
        return self.saved_version_info

    def _new_get_version_info(self,binary=None, sources=None, dm_type=None, host=None,
                device_serial=None, adb_host=None, adb_port=None):
        self.logger.info("get_version of mozversion is overrided!!!")


class MTBF_Driver:

    runner_class = MTBFTestRunner
    parser_class = GaiaTestOptions
    start_time = 0
    running_time = 0
    runner = None
    passed = 0
    failed = 0
    todo = 0
    level = 0
    end = False
    ttr = []
    ori_dir = os.path.dirname(__file__)
    dummy = os.path.join(ori_dir, "tests", "test_dummy_case.py")

    ## time format here is seconds
    def __init__(self, time, rp=None, **kwargs):
        self.duration = time
        self.rp = rp
        self.load_config(**kwargs)

    def load_config(self, **kwargs):
        parser = self.parser_class(
            usage='%prog [options] test_file_or_dir <test_file_or_dir> ...'
        )
        opts = []
        for k, v in kwargs.iteritems():
            opts.append("--" + k)
            opts.append(v)

        parser.add_option("-o", "--output", dest="archive", help="Specifying log dest folder")
        options, tests = parser.parse_args(sys.argv[1:] + opts)
        if not tests:
            tests = 'tests/test_dummy_case'  # avoid test case check, will add later
        parser.verify_usage(options, tests)
        self.options = options
        # filter empty string in testvars list
        if self.options.testvars:
            filter(lambda x: x, self.options.testvars)

        structured.commandline.add_logging_group(parser)
        logger = structured.commandline.setup_logging(
            options.logger_name, options, {"tbpl": sys.stdout})

        options.logger = logger

        self.logger = logger

        conf = []
        mtbf_conf_file = os.getenv("MTBF_CONF", os.path.join(self.ori_dir, "conf/mtbf_config.json"))

        try:
            with open(mtbf_conf_file) as json_file:
                self.conf = json.load(json_file)
        except IOError:
            logger.error("IOError on ", mtbf_conf_file)
            sys.exit(1)

        ## assign folder for logs or debugging information in a folder
        if self.options.archive:
            self.archive_folder = os.path.abspath(self.options.archive)
        else:
            self.archive_folder = os.path.join(os.getcwd(), "output")
        if not os.path.exists(self.archive_folder):
            os.makedirs(self.archive_folder)
        if not os.path.isdir(self.archive_folder):
            raise AttributeError("Output folder[" + self.archive_folder + "] has error")

        if 'level' in self.conf:
            self.level = self.conf['level']
        if 'rootdir' not in self.conf or 'workspace' not in self.conf:
            logger.error('No rootdir or workspace set, please add in config')
            sys.exit(1)

        if 'runlist' in self.conf and self.conf['runlist'].strip():
            self.runlist = self.conf['runlist']
            if not os.path.exists(self.runlist):
                self.runlist = os.path.join(self.ori_dir, self.conf['runlist'])
                if not os.path.exists(self.runlist):
                    logger.error(self.conf['runlist'], " does not exist.")
                    sys.exit(1)

        self.rootdir = os.path.join(self.ori_dir, self.conf['rootdir'])
        if not os.path.exists(self.rootdir):
            if not os.path.exists(self.rootdir):
                logger.error("Rootdir doesn't exist: " + self.rootdir)
                sys.exit(1)

        self.workspace = self.conf['workspace']
        if not os.path.exists(self.workspace):
            logger.info("Workspace doesn't exist, will create new one")
        return conf

    ## logging module should be defined here
    def start_logging(self):
        pass

    def start_gaiatest(self):
        ## Infinite run before time expired
        self.start_time = time.time()
        self.replay = os.getenv("MTBF_REPLAY")
        if self.replay:
            sg = ReplayStepGen(workspace=self.workspace, replay=self.replay)
        else:
            sg = RandomStepGen(level=self.level, root=self.rootdir, workspace=self.workspace, runlist=self.runlist, dummy=self.dummy)

        current_round = 0
        # Avoid reinitialing test env
        httpd = None
        self.logger.info("Starting MTBF....")

        # Charge x hours per 24 hours
        if os.getenv("CHARGE_HOUR"):
            self.charge = 1
        else:
            self.charge = -1

        version_info = None

        #add this for checking all test cases will continuley failed on next round
        self.retry = 0

        while(True):
            self.collect_metrics(current_round)
            current_round = current_round + 1

            ## Run test
            ## workaround: kill the runner and create another
            ## one each round, should be fixed
            for i in range(0, 10):
                try:
                    self.runner = self.runner_class(**vars(self.options))
                    break
                except NoSectionError as e:
                    self.logger.error(e)
                    continue
                except DMError as de:
                    self.logger.error("DMError catched!!!")
                    self.logger.error(sys.exc_info()[0])
                    self.logger.error(de)
                    #add sleep to wait for adb recover
                    time.sleep(5)
                    continue
            tests = sg.generate()
            file_name, file_path = zip(*tests)
            self.ttr = self.ttr + list(file_name)

            current_runtime = time.time() - self.start_time
            if self.charge > 0 and (current_runtime / 86400) >= self.charge:
                file_name = (u'test_charge.py',)
                file_path = (os.path.join(self.ori_dir, "tests", "test_charge.py"),)
                self.charge += 1
            self.logger.info("File Path: %s" % str(file_path))

            if version_info is None:
                version_info = self.runner.get_version_info(version_info)

            for i in range(0, 10):
                try:
                    self.runner.run_tests(file_path)
                    break
                except NoSectionError as e:
                    self.logger.error(e)
                    continue
                # I suggest we could catch DMerror duirng run_tests, because most of these DMError problems are not the
                # testing target of MTBF
                except DMError as de:
                    self.logger.error("DMError catched!!!")
                    self.logger.error(sys.exc_info()[0])
                    self.logger.error(de)
                    #add sleep to wait for adb recover
                    time.sleep(5)
                    continue
            # Hotfix for bug 1165231
            self.runner.mixin_run_tests = []
            for res in self.runner.results:
                res.result_modifiers = []
            self.passed = self.runner.passed + self.passed
            self.failed = self.runner.failed + self.failed
            self.todo = self.runner.todo + self.todo
            if self.runner.marionette:
                self.output_crash_report_no_to_log(self.runner.marionette.device_serial)

            current_runtime = time.time() - self.start_time
            self.logger.info("\n*Current MTBF Time: %.3f seconds" % current_runtime)
            with open("/proc/meminfo", "r") as mem_info:
                self.logger.info(mem_info.read())
            if self.charge > 0:
                self.logger.info("\n*Current Sleep Time: %.3f seconds" % ((self.charge - 1)*3600*int(os.getenv("CHARGE_HOUR"))))

            ## This is a temporary solution for stop the tests
            ## If there should be any interface there for us
            ## to detect continuous failure We can then
            ## remove this
            if self.runner.passed == 0 or self.end:
                #add this for checking all test cases will continuley failed on next round
                if self.retry == 1:
                    self.deinit()
                    break
                self.get_report()
                self.retry += 1
                time.sleep(60)


    def get_report(self):
        self.running_time = time.time() - self.start_time
        self.logger.info("\n*Total MTBF Time: %.3f seconds" % self.running_time)
        if self.charge > 0:
            self.logger.info("\nTotal Sleep Time: %.3f seconds" % ((self.charge - 1)*3600*int(os.getenv("CHARGE_HOUR"))))
        self.logger.info('\nMTBF TEST SUMMARY\n-----------------')
        self.logger.info('passed: %d' % self.passed)
        self.logger.info('failed: %d' % self.failed)
        self.logger.info('todo:   %d' % self.todo)

    def time_up(self, signum, frame):
        self.logger.info("Signal handler called with signal" + str(signum))
        self.end = True

    def deinit(self):
        virtual_home = os.getenv('VIRTUAL_ENV')
        self.get_report()
        serialized = dict()
        serialized['replay'] = self.ttr
        if self.rp:
            self.rp.write(json.dumps(serialized))
            self.rp.close()
            self.logger.info("Write reproduce steps finished")
            shutil.copy2(self.rp.name, os.path.join(self.workspace, "replay"))
        shutil.copy2(self.dummy, os.path.join(self.workspace, os.path.basename(self.dummy)))
        dest = os.path.join(self.workspace, os.path.basename(virtual_home))
        if not virtual_home == dest:
            if os.path.exists(dest):
                shutil.rmtree(dest)
            shutil.copytree(virtual_home, dest)
        info = os.path.join(virtual_home, 'info')
        if os.path.exists(info):
            shutil.copy2(info, self.workspace)

        archive_prefix = os.path.basename(self.archive_folder)
        archive_file = archive_prefix + ".zip"
        if os.path.exists(archive_file):
            num = 0
            m = re.search(archive_prefix + "_(\d+)", archive_file)
            if m:
                num = int(m.groups(0)) + 1
            archive_file = archive_prefix + "_" + str(num) + ".zip"
        with ZipFile(archive_file, "w") as archive:
            for root, dirs, files in os.walk(self.archive_folder):
                for f in files:
                    f = os.path.join(root, f)
                    archive.write(f, os.path.relpath(f, self.archive_folder))
        # Remove output folder
        shutil.rmtree(self.archive_folder)

    def collect_metrics(self, current_round):
            #current_working_folder = os.getcwd()
            ## create directory for logs or debugging information
            #os.chdir(self.archive_folder)
            out_dir = self.archive_folder

            ## import only if config file states tools is there
            if 'memory_report' in self.conf and self.conf['memory_report']:
                ## get some memory report before each round
                mem_dir = os.path.join(out_dir, "about-memory" + str(current_round))
                import tools.get_about_memory
                try:
                    tools.get_about_memory.get_and_show_info(memory_report_args(output_directory=mem_dir))
                except:
                # Ignore memory report and log it
                    self.logger.error("Crash in get-about-memory")

            ## get logcat and dmesg
            if 'logcat' in self.conf and self.conf['logcat']:
                logcat_cmd = "adb wait-for-device logcat -v threadtime -d > " + os.path.join(out_dir, "logcat" + str(current_round))
                dmesg_cmd = "adb wait-for-device shell dmesg > " + os.path.join(out_dir, "dmesg" + str(current_round))
                os.system(logcat_cmd)
                os.system(dmesg_cmd)

            ## show us the overall status of the phone
            if 'overall_status' in self.conf and self.conf['overall_status']:
                bugreport_cmd = "adb wait-for-device shell dumpstate > " + os.path.join(out_dir, "bugreport" + str(current_round))
                os.system(bugreport_cmd)

            ## show us b2g status of the phone
            if 'b2g_status' in self.conf and self.conf['b2g_status']:
                b2gps_cmd = "adb wait-for-device shell b2g-ps -t -p -P --oom > " + os.path.join(out_dir, "b2gps" + str(current_round))
                top_cmd = "adb wait-for-device shell top -m 10 -s cpu -n 1 -t >" + os.path.join(out_dir, "top" + str(current_round))
                os.system(b2gps_cmd)
                os.system(top_cmd)

                b2ginfo_cmd = "adb wait-for-device shell b2g-info > " + os.path.join(out_dir, "b2ginfo" + str(current_round))
                b2gprocrank_cmd = "adb wait-for-device shell b2g-procrank --oom > " + os.path.join(out_dir, "b2gprocrank" + str(current_round))
                os.system(b2ginfo_cmd)
                os.system(b2gprocrank_cmd)
            ## show us events
            if 'get_event' in self.conf and self.conf['get_event']:
                bugreport_cmd = "adb wait-for-device shell getevent -S > " + os.path.join(out_dir, "getevent" + str(current_round))
                os.system(bugreport_cmd)

    def output_crash_report_no_to_log(self, serial):
            ## get device crash no
            if serial in CrashScan.get_current_all_dev_serials():
                crash_result = CrashScan.get_crash_no_by_serial(serial)
                if crash_result['crashNo'] > 0:
                    self.logger.error("CrashReportFound: device " + serial + " has " + str(crash_result['crashNo']) + " crashes.")
                else:
                    self.logger.info("CrashReportNotFound: No crash report found in device " + serial)
            else:
                self.logger.error("CrashReportAdbError: Can't find device in ADB list")


def main(**kwargs):
    ## set default as 2 mins
    try:
        time = int(time2sec(os.getenv('MTBF_TIME', '2m')))
    except ValueError:

        sys.stderr.write(
            "input value parse error: ",
            os.getenv('MTBF_TIME'),
            ", format should be '1d', '10h', '10m50s'\n"
        )
    step_log = 'last_replay.txt'
    rp = None
    if not os.getenv("MTBF_REPLAY"):
        rp = open(step_log, 'w')
    mtbf = MTBF_Driver(time=time, rp=rp, **kwargs)
    if not os.getenv("MTBF_REPLAY"):
        signal.signal(signal.SIGALRM, mtbf.time_up)
        signal.alarm(mtbf.duration)
        mtbf.start_gaiatest()
        signal.alarm(0)
        return True
    else:
        mtbf.start_gaiatest()
        return True
    logcat_cmd = "adb wait-for-device logcat -v threadtime -d > last_logcat"
    os.system(logcat_cmd)


if __name__ == '__main__':
    main()
