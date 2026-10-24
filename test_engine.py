from engine import Snapshot,poisson_tail,predict,edge

def test_poisson():
    assert abs(poisson_tail(0,1))<1e-8
    assert abs(poisson_tail(1,1)-0.6321205588)<1e-5
    assert poisson_tail(1,0)==1

def test_markets_and_already_won():
    s=Snapshot(1,'L','A','B',20,'1H',0,2,total_shots=6)
    assert len(predict(s))==2
    s.half_goals=1
    assert len(predict(s))==1
    s.half_corners=3
    assert not predict(s)

def test_missing_corners():
    s=Snapshot(1,'L','A','B',20,'1H',0,None)
    assert len(predict(s))==1

def test_break_even():
    assert abs(edge(.7,1.6)['expected_return']-.12)<1e-10
