package io.lablens.workspace;

import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;
import org.springframework.scheduling.annotation.EnableScheduling;

@SpringBootApplication
@EnableScheduling
public class WorkspaceApplication {
    public static void main(String[] args) { SpringApplication.run(WorkspaceApplication.class, args); }
}
