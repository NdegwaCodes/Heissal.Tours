import { CatalogueResource } from "@/components/app/catalogue-resource";
import { DESTINATIONS } from "@/lib/catalogue";

export default function Page() {
  return <CatalogueResource spec={DESTINATIONS} />;
}
