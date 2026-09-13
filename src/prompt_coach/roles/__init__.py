"""The four roles of the loop.

- ``agent``: the teacher and the student are the same ``Agent`` class with different models/prompts.
- ``evaluator``: the judge with the answer key; one score in [0, 1] and a reason per reply.
- ``coach``: rewrites the student's prompt from its low scores; never sees the answer key.
"""
