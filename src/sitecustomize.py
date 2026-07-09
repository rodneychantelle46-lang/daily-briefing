# -*- coding: utf-8 -*-
# P7-20260709: bootstrap afternoon semantic topic de-dup guard for `python src/afternoon.py`.
try:
    from afternoon_topic_guard import install
    install()
except Exception:
    try:
        from src.afternoon_topic_guard import install
        install()
    except Exception:
        pass
