FROM maven:3.9-eclipse-temurin-25 AS build
WORKDIR /build
COPY pom.xml .
COPY src ./src
RUN --mount=type=cache,target=/root/.m2 mvn -B -q -DskipTests package

FROM eclipse-temurin:25-jre
WORKDIR /app
COPY --from=build /build/target/commerce-0.2.0.jar /app/commerce.jar
USER 10001:10001
ENTRYPOINT ["java", "-jar", "/app/commerce.jar"]
