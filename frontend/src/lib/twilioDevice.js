import { Device } from '@twilio/voice-sdk'

/**
 * Thin wrapper over the Twilio Voice JavaScript SDK.
 *
 * The SDK does not dial a destination. `device.connect()` connects this
 * browser to Twilio, which then fetches TwiML from our TwiML App's Voice URL.
 * The `params` below are delivered to that webhook as POST parameters, which
 * is how the backend learns which session this leg belongs to.
 */
export class VoiceConnection {
  constructor(handlers = {}) {
    this.device = null
    this.call = null
    this.handlers = handlers
  }

  /** Ask for the microphone explicitly so we can report a clear reason. */
  static async requestMicrophone() {
    if (!navigator.mediaDevices?.getUserMedia) {
      throw new Error('This browser cannot access the microphone.')
    }
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true })
      // We only needed the permission; the SDK opens its own stream.
      stream.getTracks().forEach((t) => t.stop())
    } catch (err) {
      if (err.name === 'NotAllowedError' || err.name === 'SecurityError') {
        throw new Error('Microphone permission was denied. Allow it and try again.')
      }
      if (err.name === 'NotFoundError') {
        throw new Error('No microphone was found on this device.')
      }
      throw new Error('Could not open the microphone.')
    }
  }

  async connect(token, params) {
    this.device = new Device(token, {
      codecPreferences: ['opus', 'pcmu'],
      logLevel: 'error',
    })

    this.device.on('error', (err) => {
      this.handlers.onError?.(this.#describe(err))
    })

    this.call = await this.device.connect({ params })

    this.call.on('accept', () => this.handlers.onAccept?.())
    this.call.on('disconnect', () => this.handlers.onDisconnect?.('remote'))
    this.call.on('cancel', () => this.handlers.onDisconnect?.('cancelled'))
    this.call.on('reject', () => this.handlers.onDisconnect?.('rejected'))
    this.call.on('reconnecting', () => this.handlers.onReconnecting?.())
    this.call.on('reconnected', () => this.handlers.onReconnected?.())
    this.call.on('error', (err) => this.handlers.onError?.(this.#describe(err)))

    // Real microphone / speaker levels, sampled by the SDK from the live
    // media stream. Used for the audio meter — nothing here is simulated.
    this.call.on('volume', (input, output) => {
      this.handlers.onVolume?.(input, output)
    })

    return this.call
  }

  mute(muted) {
    this.call?.mute(muted)
    return this.call?.isMuted() ?? false
  }

  isMuted() {
    return this.call?.isMuted() ?? false
  }

  disconnect() {
    try {
      this.call?.disconnect()
    } catch {
      /* already gone */
    }
    try {
      this.device?.destroy()
    } catch {
      /* already gone */
    }
    this.call = null
    this.device = null
  }

  /** Map SDK error codes to something a human can act on. */
  #describe(err) {
    const code = err?.code
    switch (code) {
      case 31201:
      case 31208:
        return 'Microphone permission was denied. Allow it and try again.'
      case 31005:
      case 53000:
        return 'The connection to Twilio dropped.'
      case 20101:
      case 20104:
        return 'The access token is invalid or has expired. Try again.'
      case 31003:
        return 'Could not establish a media connection. Check your network.'
      case 13224:
      case 21215:
        return 'This destination is not enabled on the Twilio account.'
      default:
        return err?.message || 'The call failed unexpectedly.'
    }
  }
}
