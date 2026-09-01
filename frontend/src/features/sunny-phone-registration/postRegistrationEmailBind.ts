export type EmailBindMailboxCategory = "microsoft" | "apple" | "domain" | "generic" | "remail";

export type EmailBindMailboxTarget = {
  email: string;
  mailbox_api: string;
  mailbox_type: string;
  mailbox_channel: string;
};

export type PostRegistrationEmailBindOptions = {
  enabled: boolean;
  category: EmailBindMailboxCategory;
  targets: EmailBindMailboxTarget[];
};

/**
 * SunnyRegister phone-registration payload only.
 * Keep these fields separate from the generic account rebind task contract.
 */
export function postRegistrationEmailBindPayload(options?: PostRegistrationEmailBindOptions) {
  return {
    bind_email_after_registration: options?.enabled === true,
    bind_mailbox_category: options?.category || "",
    bind_target_mailboxes: options?.targets || [],
  };
}
