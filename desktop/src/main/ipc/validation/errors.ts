export class ApiRequestValidationError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ApiRequestValidationError";
  }
}
