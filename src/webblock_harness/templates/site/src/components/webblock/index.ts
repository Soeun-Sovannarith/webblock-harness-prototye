/**
 * Component map shared by the generated site (Puck <Render>) and the WebBlock editor (Puck <Puck>).
 * Names and props must match the harness component registry exactly.
 */
import type { ComponentType } from "react";

import { Button } from "./Button";
import { Columns } from "./Columns";
import { ContactInfo } from "./ContactInfo";
import { ContactForm } from "./ContactForm";
import { Footer } from "./Footer";
import { Heading } from "./Heading";
import { Hero } from "./Hero";
import { Image } from "./Image";
import { Navbar } from "./Navbar";
import { ProductCardBlock } from "./ProductCard";
import { ProductDescription } from "./ProductDescription";
import { ProductGallery } from "./ProductGallery";
import { SiteRoot } from "./SiteRoot";
import { Spacer } from "./Spacer";
import { Testimonial } from "./Testimonial";
import { Text } from "./Text";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export const components: Record<string, ComponentType<any>> = {
  Navbar,
  Hero,
  Heading,
  Text,
  Button,
  Image,
  ProductDescription,
  ProductGallery,
  ProductCard: ProductCardBlock,
  Testimonial,
  ContactForm,
  ContactInfo,
  Columns,
  Spacer,
  Footer,
};

/** Slot (nested component list) props per component; must match the registry's "slot" fields. */
export const slotFields: Record<string, string[]> = {
  Columns: ["left", "right"],
};

export { SiteRoot };
export type { SiteRootProps } from "./SiteRoot";
