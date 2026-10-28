package io.lablens.workspace;

import java.util.Map;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.http.converter.HttpMessageNotReadableException;
import org.springframework.web.bind.MethodArgumentNotValidException;
import org.springframework.web.bind.MissingRequestHeaderException;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;
import org.springframework.dao.DataAccessException;

public class ApiError extends RuntimeException {
    final HttpStatus status;
    final String code;
    public ApiError(HttpStatus status, String code, String message) { super(message); this.status=status; this.code=code; }
    static ApiError bad(String message) { return new ApiError(HttpStatus.BAD_REQUEST,"invalid_request",message); }
    static ApiError missing(String message) { return new ApiError(HttpStatus.NOT_FOUND,"not_found",message); }
}

@RestControllerAdvice
class ApiErrors {
    @ExceptionHandler(ApiError.class)
    ResponseEntity<?> api(ApiError e) { return ResponseEntity.status(e.status).body(Map.of("error",e.code,"message",e.getMessage())); }
    @ExceptionHandler({MethodArgumentNotValidException.class,HttpMessageNotReadableException.class,MissingRequestHeaderException.class})
    ResponseEntity<?> invalid(Exception e) { return ResponseEntity.badRequest().body(Map.of("error","invalid_request","message","Invalid request body or missing Idempotency-Key.")); }
    @ExceptionHandler(DataAccessException.class)
    ResponseEntity<?> database(DataAccessException e) { return ResponseEntity.status(503).body(Map.of("error","database_unavailable","message","The persistent workspace is temporarily unavailable. Retry with the same Idempotency-Key.")); }
}
