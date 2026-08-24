import { CatalogueResource } from "@/components/app/catalogue-resource";
import { PARK_FEES } from "@/lib/catalogue";

export default function Page() {
  return <CatalogueResource spec={PARK_FEES} />;
}
