import threading
import pytest
from research.reset_writer_barrier import run_research_writer


@pytest.mark.parametrize('active', [True, None])
def test_active_or_unknown_reset_never_writes(active):
    gate=threading.RLock()
    def forbidden():
        pytest.fail('write during reset')
    assert run_research_writer(gate=gate,reset_active=lambda:active,write=forbidden)['status']=='SKIPPED'
    assert not gate._is_owned()


def test_another_reset_owner_blocks_writer_without_wait():
    gate=threading.RLock()
    result=[]
    with gate:
        thread=threading.Thread(target=lambda:result.append(run_research_writer(
            gate=gate,reset_active=lambda:False,write=lambda:pytest.fail('concurrent write'))))
        thread.start(); thread.join(timeout=2)
        assert not thread.is_alive()
    assert result[0]['reason_code']=='RESEARCH_WRITER_GATE_BUSY'


def test_check_and_write_share_actual_gate():
    gate=threading.RLock()
    def check():
        assert gate._is_owned()
        return False
    def write():
        assert gate._is_owned()
        return 42
    assert run_research_writer(gate=gate,reset_active=check,write=write)=={'status':'WRITTEN','result':42}


def test_failed_writer_releases_gate_and_does_not_report_success():
    gate=threading.RLock()
    def write():
        raise OSError('disk full')
    with pytest.raises(OSError):
        run_research_writer(gate=gate,reset_active=lambda:False,write=write)
    assert not gate._is_owned()
