package ru.mipt.ecom.commerce;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;

@SpringBootApplication
public class CommerceApplication {
    public static void main(String[] args) {
        if(java.util.Arrays.asList(args).contains("--migrate")) {
            var app=new SpringApplication(CommerceApplication.class);
            app.setWebApplicationType(org.springframework.boot.WebApplicationType.NONE);
            app.setAdditionalProfiles("worker","migrate");
            try(var context=app.run(args)) { /* Flyway has committed before context close. */ }
        } else SpringApplication.run(CommerceApplication.class,args);
    }
}
