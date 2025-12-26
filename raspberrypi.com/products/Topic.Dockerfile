FROM public.ecr.aws/docker/library/python:3.12-bookworm

USER root

copy batch.py /app/batch.py
COPY crawl.py requirements.txt /app/

RUN pip install --no-cache-dir -r /app/requirements.txt

CMD ["python", "/app/batch.py", "--structure_file", "/input/structure.json", "--group_index", "0", "--group_count", "16", "--output_dir", "/output/"]
