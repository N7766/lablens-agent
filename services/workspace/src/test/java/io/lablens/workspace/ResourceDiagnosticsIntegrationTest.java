package io.lablens.workspace;

import static org.junit.jupiter.api.Assertions.*;
import com.zaxxer.hikari.HikariDataSource;
import io.micrometer.core.instrument.MeterRegistry;
import java.sql.Connection;
import java.util.Map;
import java.util.concurrent.Executors;
import java.util.concurrent.TimeUnit;
import javax.sql.DataSource;
import org.apache.coyote.AbstractProtocol;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.condition.EnabledIfEnvironmentVariable;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.boot.web.embedded.tomcat.TomcatWebServer;
import org.springframework.boot.web.servlet.context.ServletWebServerApplicationContext;
import org.springframework.test.annotation.DirtiesContext;

/** Real pool contention and a real connector verify the observed budgets, without changing task state. */
@SpringBootTest(webEnvironment=SpringBootTest.WebEnvironment.RANDOM_PORT,properties={
    "lablens.dispatch-enabled=false","lablens.mq.enabled=false",
    "spring.datasource.hikari.maximum-pool-size=2","spring.datasource.hikari.minimum-idle=2",
    "spring.datasource.hikari.connection-timeout=1000",
    "server.tomcat.threads.max=17","server.tomcat.threads.min-spare=3",
    "server.tomcat.max-connections=41","server.tomcat.accept-count=7"})
@EnabledIfEnvironmentVariable(named="LABLENS_INTEGRATION_TESTS",matches="true")
@DirtiesContext(classMode=DirtiesContext.ClassMode.AFTER_CLASS)
class ResourceDiagnosticsIntegrationTest {
    @Autowired DataSource dataSource;
    @Autowired ResourceDiagnostics resources;
    @Autowired MeterRegistry metrics;
    @Autowired ServletWebServerApplicationContext application;

    @Test void fullConnectionPoolIsObservableWithoutBorrowingAnotherConnection() throws Exception {
        var pool=(HikariDataSource)dataSource;
        var acquire=metrics.get("hikaricp.connections.acquire").timer();
        double before=acquire.totalTime(TimeUnit.SECONDS);
        try (var threads=Executors.newSingleThreadExecutor();
             Connection first=pool.getConnection();Connection second=pool.getConnection()) {
            var blocked=threads.submit(()->{
                try (Connection connection=pool.getConnection()) {
                    return connection.isValid(1);
                }
            });
            try {
                long deadline=System.nanoTime()+TimeUnit.MILLISECONDS.toNanos(600);
                while (pool.getHikariPoolMXBean().getThreadsAwaitingConnection()==0 && System.nanoTime()<deadline) Thread.sleep(5);
                Map<?,?> observed=(Map<?,?>)resources.status().get("database_pool");
                assertEquals(2,observed.get("active")); assertEquals(0,observed.get("idle"));
                assertEquals(1,observed.get("pending")); assertEquals(2,observed.get("max"));
                assertEquals(1000L,observed.get("acquire_timeout_ms"));
                assertThrows(java.util.concurrent.TimeoutException.class,()->blocked.get(100,TimeUnit.MILLISECONDS));
                first.close();
                assertTrue(blocked.get(2,TimeUnit.SECONDS));
                assertTrue(acquire.totalTime(TimeUnit.SECONDS)-before>=0.09,"Hikari acquisition metric must retain the real pool wait");
                assertEquals(0,pool.getHikariPoolMXBean().getThreadsAwaitingConnection());
            } finally { first.close(); second.close(); }
        }
    }

    @Test void connectorUsesTheAdvertisedConfiguredBudgetsAndExportsThreadMetrics() {
        var server=(TomcatWebServer)application.getWebServer();
        var protocol=(AbstractProtocol<?>)server.getTomcat().getConnector().getProtocolHandler();
        assertEquals(17,protocol.getMaxThreads()); assertEquals(3,protocol.getMinSpareThreads());
        assertEquals(41,protocol.getMaxConnections()); assertEquals(7,protocol.getAcceptCount());
        Map<?,?> observed=(Map<?,?>)resources.status().get("http");
        assertEquals(protocol.getMaxThreads(),observed.get("threads_max"));
        assertEquals(protocol.getMaxConnections(),observed.get("max_connections"));
        assertEquals(17,metrics.get("tomcat.threads.config.max").gauge().value());
        assertNotNull(metrics.get("tomcat.threads.busy").gauge());
        assertNotNull(metrics.get("tomcat.threads.current").gauge());
        assertFalse(resources.status().toString().contains("jdbc:"));
    }
}
