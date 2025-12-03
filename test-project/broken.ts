// Intentionally broken TypeScript file
interface User {
  name: string;
  age: number;
}

function greet(user: User): string {
  // Error: 'foo' does not exist
  return `Hello ${user.foo}`;
}

// Error: missing required property 'age'
const myUser: User = { name: "Alice" };

console.log(greet(myUser));
