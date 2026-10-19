import Stripe from "stripe";
import { env } from "./env";

let _stripe: Stripe | undefined;

export function stripe(): Stripe {
  _stripe ??= new Stripe(env("STRIPE_SECRET_KEY"));
  return _stripe;
}

export function stripeConfigured(): boolean {
  return Boolean(process.env.STRIPE_SECRET_KEY);
}
