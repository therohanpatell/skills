'use strict';

/** Application error with an HTTP status and a machine-readable code. */
class AppError extends Error {
  constructor(message, { status = 500, code = 'INTERNAL' } = {}) {
    super(message);
    this.name = 'AppError';
    this.status = status;
    this.code = code;
  }
}

class ValidationError extends AppError {
  constructor(message) {
    super(message, { status: 400, code: 'VALIDATION' });
    this.name = 'ValidationError';
  }
}

class NotFoundError extends AppError {
  constructor(message) {
    super(message, { status: 404, code: 'NOT_FOUND' });
    this.name = 'NotFoundError';
  }
}

module.exports = { AppError, ValidationError, NotFoundError };
