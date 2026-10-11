import { ConsoleStatus } from '../../../controllers/console-status';
import { LitElement, html, css, unsafeCSS } from 'lit';
import { customElement, state } from 'lit/decorators.js';
import {
  getUserProfile,
  updateUserProfile,
  uploadAvatar,
  deleteAvatar,
  resendVerificationEmail,
  invalidateUserProfileCache,
  type UserProfile,
} from '../../../api';
import '@shoelace-style/shoelace/dist/components/input/input.js';
import '@shoelace-style/shoelace/dist/components/button/button.js';
import '../../../components/user-avatar';
import consoleStyles from '../../../styles/console-styles.css?inline';
import '../../../components/view-header';

@customElement('profile-view')
export class ProfileView extends LitElement {
  private readonly accessibilityStatus = new ConsoleStatus(this);
  @state()
  private user: UserProfile | null = null;

  @state()
  private fullName = '';

  @state()
  private updateProfileMessage = '';

  @state()
  private avatarMessage = '';

  @state() private verificationMessage = '';
  @state() private sendingVerification = false;
  @state() private refreshingVerification = false;

  async connectedCallback() {
    super.connectedCallback();
    await this.loadAccountDetails();
  }

  async loadAccountDetails() {
    try {
      this.user = await getUserProfile();
      this.fullName = this.user?.full_name || '';
    } catch (error) {
      console.error('Failed to load account details', error);
      this.updateProfileMessage = 'Failed to load account details.';
    }
  }

  async handleUpdateProfile(event: Event) {
    event.preventDefault();
    try {
      await updateUserProfile({ full_name: this.fullName });
      this.updateProfileMessage = 'Profile updated successfully.';
    } catch (error) {
      this.updateProfileMessage = 'Failed to update profile.';
    }
  }

  private async resendVerification() {
    if (!this.user || this.sendingVerification) return;
    this.sendingVerification = true;
    this.verificationMessage = '';
    try {
      this.verificationMessage = await resendVerificationEmail(this.user.email);
    } catch (error) {
      this.verificationMessage =
        error instanceof Error
          ? error.message
          : 'Failed to send verification email.';
    } finally {
      this.sendingVerification = false;
    }
  }

  private async refreshVerification() {
    if (this.refreshingVerification) return;
    this.refreshingVerification = true;
    this.verificationMessage = '';
    try {
      invalidateUserProfileCache();
      this.user = await getUserProfile();
      this.verificationMessage = this.user.email_verified
        ? 'Your email address is verified.'
        : 'Your email address is still awaiting verification. Open the link in your verification email, then check again.';
    } catch (error) {
      this.verificationMessage =
        error instanceof Error
          ? error.message
          : 'Failed to check verification status.';
    } finally {
      this.refreshingVerification = false;
    }
  }

  private _onAvatarFileSelected(event: Event) {
    const input = event.target as HTMLInputElement;
    const file = input.files?.[0];
    if (file) {
      this._uploadAvatarFile(file);
    }
  }

  private async _uploadAvatarFile(file: File) {
    this.avatarMessage = '';
    try {
      const result = await uploadAvatar(file);
      if (this.user) {
        this.user = {
          ...this.user,
          avatar_url: result.avatar_url,
          avatar_source: result.avatar_source,
        };
      }
      this.avatarMessage = 'Avatar updated.';
    } catch (error) {
      this.avatarMessage =
        error instanceof Error ? error.message : 'Failed to upload avatar.';
    }
  }

  private async _handleDeleteAvatar() {
    this.avatarMessage = '';
    try {
      await deleteAvatar();
      if (this.user) {
        this.user = {
          ...this.user,
          avatar_url: null,
          avatar_source: null,
        };
      }
      this.avatarMessage = 'Avatar removed.';
    } catch (error) {
      this.avatarMessage = 'Failed to remove avatar.';
    }
  }

  render() {
    return html`
      <view-header headerText="Profile" width="narrow"> </view-header>
      <div class="column-layout narrow">
        <div class="main-column">
          <div class="card">
            <div class="card-body">
              <div class="avatar-section">
                <user-avatar
                  .image=${this.user?.avatar_url || ''}
                  .label=${this.user?.full_name || this.user?.username || ''}
                  .seed=${this.user?.username || ''}
                  .size=${80}
                ></user-avatar>
                <div class="avatar-actions">
                  <sl-button
                    size="small"
                    @click=${() =>
                      (
                        this.renderRoot.querySelector(
                          '#avatar-input'
                        ) as HTMLInputElement
                      )?.click()}
                  >
                    Upload Photo
                  </sl-button>
                  ${
                    this.user?.avatar_url
                      ? html`<sl-button
                          size="small"
                          variant="text"
                          @click=${this._handleDeleteAvatar}
                          >Remove</sl-button
                        >`
                      : ''
                  }
                  <input
                    aria-label="Profile avatar image"
                    id="avatar-input"
                    type="file"
                    accept="image/png,image/jpeg,image/webp,image/gif"
                    style="display:none"
                    @change=${this._onAvatarFileSelected}
                  />
                </div>
                ${
                  this.avatarMessage
                    ? html`<p class="avatar-msg">${this.avatarMessage}</p>`
                    : ''
                }
              </div>

              <form @submit="${this.handleUpdateProfile}">
                <sl-input
                  label="Username"
                  .value="${this.user?.username || ''}"
                  readonly
                ></sl-input>
                <sl-input
                  label="Email"
                  .value="${this.user?.email || ''}"
                  readonly
                ></sl-input>
                ${
                  this.user
                    ? html`<div class="email-verification">
                        <p>
                          Email status:
                          <strong
                            >${this.user.email_verified ? 'Verified' : 'Not verified'}</strong
                          >
                        </p>
                        ${
                          !this.user.email_verified
                            ? html`<p>
                                  Open the link in your verification email to
                                  verify this address.
                                </p>
                                <sl-button
                                  type="button"
                                  data-testid="resend-verification"
                                  ?loading=${this.sendingVerification}
                                  ?disabled=${this.sendingVerification}
                                  @click=${this.resendVerification}
                                  >Resend verification email</sl-button
                                >
                                <sl-button
                                  type="button"
                                  data-testid="refresh-verification"
                                  ?loading=${this.refreshingVerification}
                                  ?disabled=${this.refreshingVerification}
                                  @click=${this.refreshVerification}
                                  >Check verification status</sl-button
                                >`
                            : ''
                        }
                        <p role="status" aria-live="polite">
                          ${this.verificationMessage}
                        </p>
                      </div>`
                    : ''
                }
                <sl-input
                  label="Full Name"
                  .value="${this.fullName}"
                  @sl-input="${(e: Event) =>
                    (this.fullName = (e.target as HTMLInputElement).value)}"
                ></sl-input>
                <sl-button variant="primary" type="submit"
                  >Update Profile</sl-button
                >
                ${
                  this.updateProfileMessage
                    ? html`<p>${this.updateProfileMessage}</p>`
                    : ''
                }
              </form>
            </div>
          </div>
        </div>
      </div>
    `;
  }

  static styles = [
    unsafeCSS(consoleStyles),
    css`
      h2,
      h3 {
        margin: 0;
      }
      form {
        display: flex;
        flex-direction: column;
        gap: 1rem;
      }

      .email-verification sl-button {
        width: auto;
        margin: 0 0.5rem 0.5rem 0;
      }

      .avatar-section {
        display: flex;
        align-items: center;
        gap: 1rem;
        margin-bottom: 1.5rem;
      }

      .avatar-actions {
        display: flex;
        flex-direction: column;
        gap: 0.25rem;
      }

      .avatar-msg {
        font-size: var(--sl-font-size-small);
        color: var(--sl-color-neutral-600);
      }

      sl-input[readonly]::part(base) {
        background-color: var(--sl-color-neutral-100);
        color: var(--console-meta-color);
      }

      sl-input[readonly]::part(input) {
        cursor: not-allowed;
      }

      sl-button {
        width: 12em;
      }
    `,
  ];
}
