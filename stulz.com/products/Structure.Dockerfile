FROM public.ecr.aws/docker/library/python:3.10

USER root

COPY get_structure.py requirements.txt /app/

RUN pip install --no-cache-dir -r /app/requirements.txt
