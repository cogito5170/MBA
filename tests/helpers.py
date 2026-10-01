from pathlib import Path

from mba.ir import dsl

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = ROOT / "tests" / "fixtures" / "rover.mba"
MISSION = {"GOAL": "SEARCH", "TARGET": "BLUE_OBJECT", "ENV": "SMOKE", "RISK": "LOW"}
BASE = {"camera.frame_age_ms": 10, "camera.contrast": 0.5, "power.bus_v": 12.0, "camera.crc_err_rate": 0.0,
        "radar.health": 1, "radar.n_contacts": 0, "radar.nearest_m": 99.0, "camera.blue_score": 0.0,
        "gps.fix": 1, "battery.margin_to_home": 5.0, "radar.obstacle_m": 9.0}


def model():
    return dsl.load(EXAMPLE)


def obs(**kw):
    return {**BASE, **{k.replace("__", "."): v for k, v in kw.items()}}


def scenario():
    """연기 속 탐색: 정상 -> 카메라 대비 저하(3틱 지속) -> 레이더 접촉 -> 근거리 확정 + 배터리 문턱."""
    low = dict(camera__contrast=0.1)
    return ([obs()] * 2 + [obs(**low)] * 4 + [obs(**low, radar__n_contacts=1, radar__nearest_m=5.0)] * 2
            + [obs(**low, radar__n_contacts=1, radar__nearest_m=1.5, camera__blue_score=0.9,
                   battery__margin_to_home=-1.0)])
