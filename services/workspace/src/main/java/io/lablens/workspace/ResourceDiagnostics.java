package io.lablens.workspace;

import com.zaxxer.hikari.HikariDataSource;
import java.util.LinkedHashMap;
import java.util.Map;
import javax.sql.DataSource;
import org.springframework.boot.autoconfigure.web.ServerProperties;
import org.springframework.stereotype.Service;

/** Process-local snapshots only: observing pool pressure never borrows a database connection. */
@Service
public class ResourceDiagnostics {
    private final DataSource dataSource;
    private final ServerProperties server;

    public ResourceDiagnostics(DataSource dataSource,ServerProperties server) {
        this.dataSource=dataSource; this.server=server;
    }

    public Map<String,Object> status() {
        Map<String,Object> result=new LinkedHashMap<>();
        result.put("scope","per_jvm_process_point_in_time");
        Map<String,Object> database=new LinkedHashMap<>();
        if (dataSource instanceof HikariDataSource pool) {
            database.put("implementation","HikariCP");
            database.put("max",pool.getMaximumPoolSize());
            database.put("min_idle",pool.getMinimumIdle());
            database.put("acquire_timeout_ms",pool.getConnectionTimeout());
            var observed=pool.getHikariPoolMXBean();
            database.put("initialized",observed!=null);
            if (observed!=null) {
                database.put("active",observed.getActiveConnections());
                database.put("idle",observed.getIdleConnections());
                database.put("total",observed.getTotalConnections());
                database.put("pending",observed.getThreadsAwaitingConnection());
            }
        } else database.put("implementation","unobserved");
        result.put("database_pool",database);
        var tomcat=server.getTomcat();
        result.put("http",Map.of("implementation","Tomcat_platform_threads",
            "threads_max",tomcat.getThreads().getMax(),"threads_min_spare",tomcat.getThreads().getMinSpare(),
            "max_connections",tomcat.getMaxConnections(),"accept_count",tomcat.getAcceptCount(),
            "configuration_scope","connector_configuration_not_measured_capacity"));
        result.put("admission_timing_scope","guard statement includes SQL roundtrip; transaction timer excludes Redis and connection acquisition; joined caller time ends at its actual commit or rollback");
        return result;
    }
}
