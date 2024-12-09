FROM e2bdev/code-interpreter:latest

# Install Poetry
RUN curl -sSL https://install.python-poetry.org | python3 -
ENV PATH="/root/.local/bin:${PATH}"

WORKDIR /home/user
COPY pyproject.toml poetry.lock* /home/user/
RUN poetry install --no-root
