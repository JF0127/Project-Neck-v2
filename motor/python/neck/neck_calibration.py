"""离线加载 Project-Neck 标定数据；不导入控制代码、不连接硬件。"""

import json
from pathlib import Path

import numpy as np

from .neck_kinematics import Calibration, NeckModel


DEFAULT_CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "neck_calibration.json"
MODEL_JOINTS = ("q1", "q2", "q3")


def _finite_number(value, field):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} 必须是数值")
    if not np.isfinite(value):
        raise ValueError(f"{field} 必须是有限数值")
    return float(value)


def load_neck_model(config_path=DEFAULT_CONFIG_PATH):
    """读取 JSON，返回 (model, soft_limits_deg, id_to_model)。

    model: 默认 Geometry + 配置的 Calibration / min_margin_deg。
        配置 offset_deg 转为弧度后写入 Calibration.offset；允许暂定零位。
    soft_limits_deg: shape (3, 2) 的 NumPy 数组，行固定为 q1/q2/q3，
        列为 [lower_deg, upper_deg]，均为驱动器输出端绝对角度。
    id_to_model: {电机 ID: "q1"/"q2"/"q3"}，不依赖 JSON 数组排列顺序。

    不修改原始模型，也不将软限位注入 info['ok']；调用方需另行检查软限位。
    配置结构/数值不合法时抛出 ValueError；文件读取失败抛出 OSError。
    """
    with Path(config_path).open(encoding="utf-8") as stream:
        config = json.load(stream)

    try:
        if type(config["schema_version"]) is not int or config["schema_version"] != 1:
            raise ValueError("不支持的 schema_version（当前仅支持 1）")
        margin_deg = _finite_number(config["soft_limits"]["margin_deg"], "margin_deg")
        min_margin_deg = _finite_number(config["model"]["min_margin_deg"], "min_margin_deg")
        if margin_deg < 0:
            raise ValueError("margin_deg 不得为负")
        if not 0 <= min_margin_deg <= 90:
            raise ValueError("min_margin_deg 必须在 [0, 90] 度内")

        motors = config["motors"]
        if not isinstance(motors, list) or len(motors) != len(MODEL_JOINTS):
            raise ValueError("motors 必须恰好包含三个电机")
        by_joint = {}
        id_to_model = {}
        for motor in motors:
            motor_id = motor["id"]
            joint = motor["mapping"]["model_joint"]
            if type(motor_id) is not int or motor_id <= 0 or motor_id in id_to_model:
                raise ValueError("电机 ID 必须为互不重复的正整数")
            if joint not in MODEL_JOINTS or joint in by_joint:
                raise ValueError("模型映射必须恰好覆盖 q1、q2、q3 且无重复")
            by_joint[joint] = motor
            id_to_model[motor_id] = joint

        signs, offsets_deg, soft_limits_deg = [], [], []
        for joint in MODEL_JOINTS:
            motor = by_joint[joint]
            endpoints = motor["mechanical_range"]["endpoints"]
            if not isinstance(endpoints, list) or len(endpoints) != 2:
                raise ValueError(f"{joint} 必须有两个机械端点")
            low_deg, high_deg = sorted(
                _finite_number(endpoint["angle_deg"], f"{joint}.angle_deg")
                for endpoint in endpoints
            )
            lower_deg, upper_deg = low_deg + margin_deg, high_deg - margin_deg
            if lower_deg >= upper_deg:
                raise ValueError(f"{joint} 机械范围收进 margin 后无有效行程")

            calibration = motor["calibration"]
            sign = _finite_number(calibration["sign"], f"{joint}.sign")
            offset_deg = _finite_number(calibration["offset_deg"], f"{joint}.offset_deg")
            if sign not in (-1, 1):
                raise ValueError(f"{joint}.sign 必须是 +1 或 -1")
            if not lower_deg <= offset_deg <= upper_deg:
                raise ValueError(f"{joint}.offset_deg 超出软限位")
            signs.append(sign)
            offsets_deg.append(offset_deg)
            soft_limits_deg.append((lower_deg, upper_deg))
    except (KeyError, TypeError, IndexError) as exc:
        raise ValueError(f"标定配置结构不合法: {exc}") from exc

    calibration = Calibration(
        sign=np.asarray(signs, dtype=float),
        offset=np.radians(offsets_deg),
    )
    model = NeckModel(calib=calibration, min_margin_deg=min_margin_deg)
    return model, np.asarray(soft_limits_deg, dtype=float), id_to_model
