# Docker setup

## Linux

### Run docker-compose
* At the root of the project, set up environment variables in `.env` per example at [.env.example](../.env.example)
* Build the container
  ```bash
  docker compose --build --no-cache
  ```
* You can run the container with embedded MongoDB with the following command:
  ```bash
  docker compose up --profile with-mongodb
  ```
* Or, you can set the `MONGODB_URI` environment variable to point to a remote MongoDB server and run without embedded MongoDB with the following command:
  ```bash
  docker compose up --profile default
  ```
* The application will be available at [http://localhost:3000](http://localhost:3000)
* When restarting the application, run `docker compose down --profile with-mongodb`, or `docker compose down --profile default` first.

### Build the container and push it to Docker Hub
* At the root of the project, run
  ```bash
  docker build -t analytiqhub/doc-router:latest .
  docker login
  docker push analytiqhub/doc-router:latest
  ```