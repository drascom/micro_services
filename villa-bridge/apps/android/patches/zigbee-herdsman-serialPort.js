"use strict";

Object.defineProperty(exports, "__esModule", { value: true });
exports.SerialPort = void 0;

const { SerialPortStream } = require("@serialport/stream");

let detectedBinding;

function getDetectedBinding() {
  if (!detectedBinding) {
    const { autoDetect } = require("@serialport/bindings-cpp");
    detectedBinding = autoDetect();
  }
  return detectedBinding;
}

class SerialPort extends SerialPortStream {
  static async list() {
    return getDetectedBinding().list();
  }

  static get binding() {
    return getDetectedBinding();
  }

  constructor(options, openCallback) {
    super({
      binding: getDetectedBinding(),
      ...options
    }, openCallback);
  }

  async asyncOpen() {
    return await new Promise((resolve, reject) => {
      this.open((error) => error ? reject(error) : resolve());
    });
  }

  async asyncClose() {
    return await new Promise((resolve, reject) => {
      this.close((error) => error ? reject(error) : resolve());
    });
  }

  async asyncFlush() {
    return await new Promise((resolve, reject) => {
      this.flush((error) => error ? reject(error) : resolve());
    });
  }

  async asyncFlushAndClose() {
    await this.asyncFlush();
    await this.asyncClose();
  }

  async asyncGet() {
    return await new Promise((resolve, reject) => {
      this.get((error, options) => error ? reject(error) : resolve(options));
    });
  }

  async asyncSet(options) {
    return await new Promise((resolve, reject) => {
      this.set(options, (error) => error ? reject(error) : resolve());
    });
  }
}

exports.SerialPort = SerialPort;
