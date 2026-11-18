FROM maven:3.9-eclipse-temurin-25 AS build
WORKDIR /build
COPY pom.xml .
COPY src ./src
RUN --mount=type=cache,target=/root/.m2 mvn -B -q verify
FROM eclipse-temurin:25-jre
WORKDIR /app
COPY --from=build /build/target/provider-0.4.0.jar /app/app.jar
USER 10001:10001
ENTRYPOINT ["java","-jar","/app/app.jar"]
