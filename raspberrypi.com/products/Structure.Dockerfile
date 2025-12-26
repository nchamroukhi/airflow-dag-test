FROM public.ecr.aws/docker/library/python:3.12-bookworm

USER root

COPY get_structure.py requirements.txt /app/

RUN pip install --no-cache-dir -r /app/requirements.txt

CMD ["python", "/app/get_structure.py", "--out", "output/topic_structure.json"]
