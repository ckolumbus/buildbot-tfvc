from io import StringIO
import re
import xml.dom.minidom
import xml.parsers.expat
from xml.etree import ElementTree

from twisted.internet import defer
#from twisted.internet import reactor
from twisted.python import log

from buildbot.process import buildstep
from buildbot.process import remotecommand
from buildbot.process import results
from buildbot.plugins import secrets, util
from buildbot.steps.source.base import Source

from buildbot.config import ConfigErrors
from buildbot.interfaces import WorkerSetupError

class TFVC(Source):

    """I perform TFVC checkout/update operations."""

    name = 'tfvc'

    renderables = ['repourl', 'password']
    #secrets = ['password']
    possible_methods = ('clean', 'fresh', 'clobber', None)

    def __init__(self, repourl=None, branch=None, branchdir='s',
                 mode='incremental', method=None,
                 tfbin='tf.exe',
                 map=None, cloak=None,
                 logintype=None, username=None, password=None, 
                 extra_args = None,
                 **kwargs):

        self.repourl = repourl
        self.branch = branch
        self.branchdir = branchdir
        self.username = username
        self.password = password
        self.method = method
        self.mode = mode
        self.extra_args = extra_args
        self.tfbin = tfbin
        self.workspace = None
        self.cloak = cloak
        self.map = map

        super().__init__(**kwargs)
        errors = []

        if not self._hasAttrGroupMember('mode', self.mode):
            errors.append("mode {} is not one of {}".format(self.mode,
                                                            self._listAttrGroupMembers('mode')))
        if self.method not in self.possible_methods:
            errors.append("method {} is not one of {}".format(self.method, self.possible_methods))

        if repourl is None:
            errors.append("you must provide repourl")

        if branch is None:
            errors.append("you must provide a branch path")

        if errors:
            raise ConfigErrors(errors)
        

    @defer.inlineCallbacks
    def run_vc(self, branch, revision, patch):
        self.workspace = f"bb_{self.build.builder.name}"

        self.revision = revision
        #self.method = self._getMethod()
        self.stdio_log = yield self.addLogForRemoteCommands("stdio")

        installed = yield self.checkTf()
        if not installed:
            raise WorkerSetupError("TF.exe is not found on worker")

        yield self._setup_workspace()
        yield self._map(self.branch, self.branchdir)

        if self.cloak:
            for p in self.cloak:
                yield self._cloak(f"{self.branch}/{p}")

        if self.map:
            for p,d in self.map:
                yield self._map(f"{self.branch}/{p}", d)


        yield self._getAttrGroupMember('mode', self.mode)()

        return results.SUCCESS


    @defer.inlineCallbacks
    def checkTf(self):
        cmd = remotecommand.RemoteShellCommand(self.workdir, [self.tfbin],
                                               env=self.env,
                                               logEnviron=False,
                                               timeout=self.timeout)
        cmd.useLog(self.stdio_log, False)
        yield self.runCommand(cmd)
        return cmd.rc == 0

    @defer.inlineCallbacks
    def mode_full(self):
        #self.worker.conn.remotePrint(message="XXXXX clean checkout")
        log.msg("performing clean checkout")
        yield self.clobber()

    @defer.inlineCallbacks
    def mode_incremental(self):
        #self.worker.conn.remotePrint(message="XXXXX try incremental checkout")
        updatable = yield self._sourcedirIsUpdatable()
        if not updatable:
            log.msg("workspace not updatable, performing clean checkout")
            # blow away the old (un-updatable) directory and checkout
            yield self.clobber()
        else:
            log.msg("updating existing workspace")
            # otherwise, do an update
            yield self._get()

    @defer.inlineCallbacks
    def clobber(self):
        yield self.runRmdir(self.workdir, timeout=self.timeout)
        yield self._get()

    @defer.inlineCallbacks
    def _setup_workspace(self):

        command = ['vc', 'workspaces', '/format:xml', f"/collection:{self.repourl}" ]
        stdout = yield self._dovccmd(command, collectStdout=True)

        try:
            stdout_xml = xml.dom.minidom.parseString(stdout)
            workspaces = [i.getAttribute("name") for i in stdout_xml.getElementsByTagName('Workspace')]

        except xml.parsers.expat.ExpatError as e:
            yield self.stdio_log.addHeader("Corrupted workspace xml, aborting step")
            raise buildstep.BuildStepFailed() from e

        if not self.workspace in workspaces:
            command = ['vc', 'workspace', '/new', 
                self.workspace,
                f"/location:server",
                f"/collection:{self.repourl}",
                f'/comment:buildbot workspace for builder {self.build.builder.name} on worker {self.worker.name}'
            ]
            yield self._dovccmd(command)

            # delete auto mapped $/ done by general cleanup below
            command = ['vc', 'workfold', '/unmap', 
                f'/workspace:{self.workspace}',
                f'$/'
            ]
            yield self._dovccmd(command)

        else:
            # clean up existing workspace
            doc = ElementTree.XML(stdout)
            workspace = doc.find(f'.//Workspace[@name="{self.workspace}"]')
            folders = workspace.findall('.//WorkingFolder')
            log.msg([f.attrib for f in folders])

            for element in folders:
                e = element.attrib
                #if 'type' in e and e['type'].lower() == "cloak":
                #    log.msg(f"decloaking : {e['item']}")
                #    yield self._decloak(e['item'])
                #elif 'local' in e:
                # unmapping removes associated cloakings as well
                if 'local' in e:
                    log.msg(f"unmapping : {e['item']}")
                    yield self._unmap(e['item'])
            

    @defer.inlineCallbacks
    def _cloak(self, path):
        command = ['vc', 'workfold', '/cloak', f"/workspace:{self.workspace}", path]
        yield self._dovccmd(command)
        # deletion of existing directories/files done by 'get'

    @defer.inlineCallbacks
    def _decloak(self, path):
        command = ['vc', 'workfold', '/decloak', f"/workspace:{self.workspace}", path ]
        yield self._dovccmd(command, abandonOnFailure=False)

    @defer.inlineCallbacks
    def _map(self, path, dest):
        command = ['vc', 'workfold', f"/workspace:{self.workspace}", '/map', path, dest] 
        yield self._dovccmd(command, abandonOnFailure=False)

    @defer.inlineCallbacks
    def _unmap(self, path):
        command = ['vc', 'workfold', '/unmap', 
            f'/workspace:{self.workspace}',
            path
        ]
        yield self._dovccmd(command)

    @defer.inlineCallbacks
    def _get(self):
        command = ['vc', 'get']
        if self.revision:
            command.extend([f'/version:{str(self.revision)}'])
        command.extend(['/recursive', '/overwrite', '.'])

        yield self._dovccmd(command)


    def computeSourceRevision(self, changes):
        if not changes:
            return None
        return changes[-1].revision

    @defer.inlineCallbacks
    def _sourcedirIsUpdatable(self):
        # tf.exe vc info <workdir> --> ServerPath must match branch

        exists = yield self.pathExists(self.workdir)
        if not exists:
            return False

        # then run 'tf vc info --xml' to check that the URL matches our repourl
        stdout, stderr = yield self._dovccmd(['vc', 'info', self.branchdir], collectStdout=True,
                                             collectStderr=True, abandonOnFailure=False)

        dir_branch = None
        buf = StringIO(stdout)
        m = re.compile(r'\s*Server path:\s*(.*)\s*$')
        for i in buf:
            f= m.match(i.strip())
            if (f):
                dir_branch = f.group(1)

        if dir_branch:
            log.msg("found branch : ", dir_branch)
        else:
            log.msg("no branch information found in working dir, forcing clean checkout")

        return dir_branch == self.branch

    @defer.inlineCallbacks
    def _dovccmd(self, command, collectStdout=False, collectStderr=False, abandonOnFailure=True, addlogin=True):
        assert command, "No command specified"
        command.extend(['/noprompt'])
        if self.username and self.password and addlogin:
            command.extend([f"/login:{self.username},{self.password}"])
        if self.extra_args:
            command.extend(self.extra_args)

        cmd = remotecommand.RemoteShellCommand(self.workdir, [self.tfbin] + command,
                                               env=self.env,
                                               logEnviron=self.logEnviron,
                                               timeout=self.timeout,
                                               collectStdout=collectStdout,
                                               collectStderr=collectStderr)
        cmd.useLog(self.stdio_log, False)
        yield self.runCommand(cmd)

        # does not make sense to logEnviron for each command (just for first)
        self.logEnviron = False

        if cmd.didFail() and abandonOnFailure:
            log.msg("Source step failed while running command {}".format(cmd))
            raise buildstep.BuildStepFailed()
        if collectStdout and collectStderr:
            return (cmd.stdout, cmd.stderr)
        elif collectStdout:
            return cmd.stdout
        elif collectStderr:
            return cmd.stderr
        return cmd.rc