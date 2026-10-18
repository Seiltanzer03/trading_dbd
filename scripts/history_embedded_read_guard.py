"""Embed the shared stdlib guard around an existing remote SQLite lifetime."""
import inspect
import textwrap
from seiltanzer import history_live_read_guard


def guard_program(program):
    start = program.index("c=sqlite3.connect(")
    end = program.index('c.close()\n',start)+len('c.close()\n')
    connect,body = program[start:end].split('\n',1)
    body = body.removesuffix('c.close()\n')
    return (program[:start]+'\n'+inspect.getsource(history_live_read_guard)+
        "\nwith guarded_live_read(Path('/opt/seiltanzer/data/trades.db')):\n"+
        textwrap.indent(connect+'\ntry:\n'+textwrap.indent(body,'    ')+
                        'finally:\n    c.close()\n','    ')+program[end:])
