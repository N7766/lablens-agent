FROM maven:3.9-eclipse-temurin-21 AS build
WORKDIR /build
COPY pom.xml .
COPY src ./src
RUN mvn -B -q package

FROM eclipse-temurin:21-jre-jammy
WORKDIR /app
COPY --from=build /build/target/lablens-workspace-0.12.0.jar /app/workspace.jar
USER 10001
EXPOSE 8081
ENV LABLENS_WORKSPACE_BIND=0.0.0.0
ENTRYPOINT ["java", "-jar", "/app/workspace.jar"]
