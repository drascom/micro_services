"""A fake ``pi`` process for the tests of the agent runner / the repair agent (no real pi, LLM or network).

``FakeProc`` stands in for ``subprocess.Popen``: its ``stdout`` yields JSON event lines in the shape of the Faz 2b spike
(pi 0.99.1), ``hooks[i](proc)`` runs right before line ``i`` (that is where a test performs what the agent's tool call would
do on the server, e.g. ``sandbox._do_submit_repair``), ``hang`` keeps stdout open until ``terminate()`` / ``kill()``.
Import it with ``from _fake_pi import ...`` (the tests directory is on ``sys.path``)."""
import json
import subprocess
import threading


def ev(**kw):
    return json.dumps(kw)


def tool_start(name, args, call="c1"):
    return ev(type="tool_execution_start", toolCallId=call, toolName=name, args=args)


def tool_end(name, text, call="c1", error=False):
    return ev(type="tool_execution_end", toolCallId=call, toolName=name, isError=error,
              result={"content": [{"type": "text", "text": text}]})


def msg_end(text, role="assistant"):
    return ev(type="message_end", message={"role": role, "content": [{"type": "text", "text": text}]})


def good_end(text="Klaar."):
    """A normal final assistant message + turn_end + agent_end (``stopReason: "stop"``)."""
    message = {"role": "assistant", "content": [{"type": "text", "text": text}], "stopReason": "stop"}
    return [ev(type="message_end", message=message), ev(type="turn_end", message=message, toolResults=[]),
            ev(type="agent_end", messages=[message], willRetry=False), ev(type="agent_settled")]


def provider_failure(text="Codex error: the model is not supported"):
    message = {"role": "assistant", "content": [], "stopReason": "error", "errorMessage": text}
    return [ev(type="message_end", message=message), ev(type="turn_end", message=message, toolResults=[]),
            ev(type="agent_end", messages=[message], willRetry=False), ev(type="agent_settled")]


class Stdin:
    def __init__(self):
        self.data, self.closed = "", False

    def write(self, text):
        self.data += text

    def close(self):
        self.closed = True


class FakeProc:
    pid = 4242

    def __init__(self, lines=(), rc=0, stderr="", hang=False, hooks=None):
        self.lines, self.rc, self.hang, self.hooks = list(lines), rc, hang, hooks or {}
        self.stdin = Stdin()
        self.stderr = iter(stderr.splitlines(True))
        self.returncode = None
        self.signals = []
        self.job_id = self.cmd = self.kw = None
        self.started = threading.Event()
        self._killed, self._done = threading.Event(), threading.Event()
        self.stdout = self._gen()

    def _gen(self):
        self.started.set()
        for i, line in enumerate(self.lines):
            if i in self.hooks:
                self.hooks[i](self)
            yield line + "\n"
        if self.hang:
            self._killed.wait(15)
        self._done.set()

    def terminate(self):
        self.signals.append("TERM")
        self.returncode = -15
        self._killed.set()

    def kill(self):
        self.signals.append("KILL")
        self.returncode = -9
        self._killed.set()

    def poll(self):
        return self.returncode if self._done.is_set() else None

    def wait(self, timeout=None):
        if not self._done.wait(timeout if timeout is not None else 15):
            raise subprocess.TimeoutExpired("pi", timeout)
        if self.returncode is None:
            self.returncode = self.rc
        return self.returncode

    def option(self, flag):
        return self.cmd[self.cmd.index(flag) + 1]
