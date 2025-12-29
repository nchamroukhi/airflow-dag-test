FROM public.ecr.aws/docker/library/python:3.10

USER root

WORKDIR /app

COPY batch.py /app/batch.py
COPY crawl.py requirements.txt /app/

RUN pip install --no-cache-dir -r /app/requirements.txt
